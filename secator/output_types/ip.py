import time
from dataclasses import dataclass, field
from enum import Enum

from secator.definitions import ALIVE, IP
from secator.output_types import OutputType
from secator.utils import rich_to_ansi


class IpProtocol(str, Enum):
	IPv6 = 'IPv6'
	IPv4 = 'IPv4'


@dataclass
class Ip(OutputType):
	ip: str
	host: str = field(default='', repr=True, compare=False)
	alive: bool = False
	protocol: str = field(default=IpProtocol.IPv4)
	_source: str = field(default='', repr=True, compare=False)
	_type: str = field(default='ip', repr=True)
	_timestamp: int = field(default_factory=lambda: time.time(), compare=False)
	_uuid: str = field(default='', repr=True, compare=False)
	_context: dict = field(default_factory=dict, repr=True, compare=False)
	_tagged: bool = field(default=False, repr=True, compare=False)
	_duplicate: bool = field(default=False, repr=True, compare=False)
	_related: list = field(default_factory=list, compare=False)

	_table_fields = [IP, ALIVE]
	_sort_by = (IP,)

	def __str__(self) -> str:
		return self.ip

	def __repr__(self) -> str:
		s = f'💻 [bold white]{self.ip}[/]'
		if self.host:
			s += rf' \[[bold magenta]{self.host}[/]]'
		if self.alive:
			s += r' [bold green]🟢[/]'
		return rich_to_ansi(s)
