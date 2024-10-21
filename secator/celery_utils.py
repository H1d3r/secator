from celery.result import AsyncResult, GroupResult
from rich.panel import Panel
from rich.padding import Padding
from rich.progress import Progress as RichProgress, SpinnerColumn, TextColumn, TimeElapsedColumn
from contextlib import nullcontext
from secator.definitions import STATE_COLORS
from secator.utils import debug, traceback_as_string
from secator.rich import console
from secator.config import CONFIG
import kombu
import kombu.exceptions
from time import sleep


class CeleryData(object):
	"""Utility to simplify tracking a Celery task and all of its subtasks."""

	def iter_results(
			result,
			ids_map={},
			description=True,
			refresh_interval=CONFIG.runners.poll_frequency,
			print_remote_info=True,
			print_remote_title='Results'
		):
		"""Generator to get results from Celery task.

		Args:
			result (Union[AsyncResult, GroupResult]): Celery result.
			description (bool): Whether to show task description.
			refresh_interval (int): Refresh interval.
			print_remote_info (bool): Whether to display live results.
			print_remote_title (str): Title for the progress panel.

		Yields:
			dict: Subtasks state and results.
		"""
		# Display live results if print_remote_info is set
		if print_remote_info:
			class PanelProgress(RichProgress):
				def get_renderables(self):
					yield Padding(Panel(
						self.make_tasks_table(self.tasks),
						title=print_remote_title,
						border_style='bold gold3',
						expand=False,
						highlight=True), pad=(2, 0, 0, 0))
			from rich.console import Console
			console = Console()
			progress = PanelProgress(
				SpinnerColumn('dots'),
				TextColumn('{task.fields[descr]}  ') if description else '',
				TextColumn('[bold cyan]{task.fields[full_name]}[/]'),
				TextColumn('{task.fields[state]:<20}'),
				TimeElapsedColumn(),
				TextColumn('{task.fields[count]}'),
				TextColumn('{task.fields[progress]}%'),
				# TextColumn('\[[bold magenta]{task.fields[id]:<30}[/]]'),  # noqa: W605
				refresh_per_second=1,
				transient=False,
				console=console,
				# redirect_stderr=True,
				# redirect_stdout=False
			)
		else:
			progress = nullcontext()

		with progress:

			# Make initial progress
			if print_remote_info:
				progress_cache = CeleryData.init_progress(progress, ids_map)

			# Get live results and print progress
			for data in CeleryData.poll(result, ids_map, refresh_interval):
				yield from data['results']

				if print_remote_info:
					task_id = data['id']
					progress_id = progress_cache[task_id]
					CeleryData.update_progress(progress, progress_id, data)

			# Update all tasks to 100 %
			if print_remote_info:
				for progress_id in progress_cache.values():
					progress.update(progress_id, advance=100)

	@staticmethod
	def init_progress(progress, ids_map):
		cache = {}
		for task_id, data in ids_map.items():
			pdata = data.copy()
			state = data['state']
			pdata['state'] = f'[{STATE_COLORS[state]}]{state}[/]'
			id = progress.add_task('', advance=0, **pdata)
			cache[task_id] = id
		return cache

	@staticmethod
	def update_progress(progress, progress_id, data):
		"""Update rich progress with fresh data."""
		pdata = data.copy()
		state = data['state']
		pdata['state'] = f'[{STATE_COLORS[state]}]{state}[/]'
		pdata = {k: v for k, v in pdata.items() if v}
		progress_int = pdata.pop('progress', None)
		progress.update(progress_id, **pdata)
		if progress_int:
			progress.update(progress_id, advance=progress_int, **pdata)

	@staticmethod
	def poll(result, ids_map, refresh_interval):
		"""Poll Celery subtasks results in real-time. Fetch task metadata and partial results from each task that runs.

		Yields:
			dict: Subtasks state and results.
		"""
		while True:
			try:
				yield from CeleryData.get_all_data(result, ids_map)
				if result.ready():
					debug('RESULT READY', sub='celery.runner', id=result.id)
					yield from CeleryData.get_all_data(result, ids_map)
					break
			except kombu.exceptions.DecodeError:
				debug('kombu decode error', sub='celerydebug', id=result.id)
				pass
			finally:
				sleep(refresh_interval)

	@staticmethod
	def get_all_data(result, ids_map):
		"""Get Celery results from main result object, AND all subtasks results.

		Yields:
			dict: Subtasks state and results.
		"""
		task_ids = list(ids_map.keys())
		datas = []
		for task_id in task_ids:
			data = CeleryData.get_task_data(task_id, ids_map)
			if not data:
				continue
			debug(
				'POLL',
				sub='celery.poll',
				id=data['id'],
				obj={data['full_name']: data['state'], 'count': data['count']},
				level=4
			)
			yield data
			datas.append(data)

		# Calculate and yield progress
		if not datas:
			return
		total = len(datas)
		count_finished = sum([i['ready'] for i in datas if i])
		percent = int(count_finished * 100 / total) if total > 0 else 0
		data = datas[-1]
		data['progress'] = percent
		yield data

	@staticmethod
	def get_task_data(task_id, ids_map):
		"""Get task info.

		Args:
			task_id (str): Celery task id.

		Returns:
			dict: Task info (id, name, state, results, chunk_info, count, error, ready).
		"""

		# Get task data
		data = ids_map.get(task_id, {})
		# if not data:
		# 	debug('task not in ids_map', sub='celerydebug', id=task_id)
		# 	return

		# Get remote result
		res = AsyncResult(task_id)
		if not res:
			debug('empty response', sub='celerydebug', id=task_id)
			return

		# Set up task state
		data.update({
			'state': res.state,
			'ready': False,
			'results': []
		})

		# Get remote task data
		info = res.info

		# Depending on the task state, info will be either an Exception (FAILURE), a list (SUCCESS), or a dict (RUNNING).
		# - If it's an Exception, it's an unhandled error.
		# - If it's a list, it's the task results.
		# - If it's a dict, it's the custom user metadata.

		if isinstance(info, Exception):
			debug('unhandled exception', obj={'msg': str(info), 'tb': traceback_as_string(info)}, sub='celerydebug', id=task_id)
			raise info

		elif isinstance(info, list):
			data['results'] = info
			errors = [e for e in info if e._type == 'error']
			data['count'] = len([c for c in info if c._source == data['name']])
			data['state'] = 'FAILURE' if errors else 'SUCCESS'

		elif isinstance(info, dict):
			data.update(info)

		# Set ready flag and progress
		data['ready'] = data['state'] in ['FAILURE', 'SUCCESS', 'REVOKED']
		if data['ready']:
			data['progress'] = 100
		elif data['results']:
			progresses = [e for e in data['results'] if e._type == 'progress']
			if progresses:
				data['progress'] = progresses[-1].percent
				# print(f'found progress for {data["full_name"]}: {data["progress"]}')

		debug('data', obj=data, sub='celerydebug', id=task_id)
		return data

	@staticmethod
	def get_task_ids(result, ids=[]):
		"""Get all Celery task ids recursively.

		Args:
			result (Union[AsyncResult, GroupResult]): Celery result object.
			ids (list): List of ids.
		"""
		if result is None:
			return

		try:
			if isinstance(result, GroupResult):
				CeleryData.get_task_ids(result.parent, ids=ids)

			elif isinstance(result, AsyncResult):
				if result.id not in ids:
					ids.append(result.id)

			if hasattr(result, 'children'):
				children = result.children
				if isinstance(children, list):
					for child in children:
						CeleryData.get_task_ids(child, ids=ids)

			# Browse parent
			if hasattr(result, 'parent') and result.parent:
				CeleryData.get_task_ids(result.parent, ids=ids)

		except kombu.exceptions.DecodeError as e:
			console.print(f'[bold red]{str(e)}. Aborting get_task_ids.[/]')
			return
