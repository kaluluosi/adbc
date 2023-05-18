import asyncio
import re
from dataclasses import field, dataclass
import typing
from typing import Optional
from dataclasses_json import dataclass_json

from . import Plugin

if typing.TYPE_CHECKING:
    from async_adbc.device import Device

CPUStatMap = dict[int, "CPUStat"]
CPUUsageMap = dict[int, "CPUUsage"]


@dataclass_json
@dataclass
class CPUInfo:
    platform: str
    name: str
    abi: str
    core: int
    freq: tuple[int, int]


@dataclass_json
@dataclass
class CPUUsage:
    usage: float = field(default=0.0)
    normalized: float = field(default=0.0)


@dataclass_json
@dataclass
class CPUFreq:
    min: int
    cur: int
    max: int


@dataclass_json
@dataclass
class CPUStat:
    def __init__(
        self,
        user: float = 0,
        nice: float = 0,
        system: float = 0,
        idle: float = 0,
        iowait: float = 0,
        irq: float = 0,
        softirq: float = 0,
        stealstolen: float = 0,
        guest: float = 0,
        guest_nice: float = 0,
    ):
        self.user = user
        self.nice = nice
        self.system = system
        self.idle = idle
        self.iowait = iowait
        self.irq = irq
        self.softirq = softirq
        self.stealstolen = stealstolen
        self.guest = guest
        self.guest_nice = guest_nice

    @property
    def total(self):
        return (
            self.user
            + self.nice
            + self.system
            + self.idle
            + self.iowait
            + self.irq
            + self.softirq
            + self.stealstolen
            + self.guest
            + self.guest_nice
        )

    @property
    def usage(self) -> float:
        """
        获取占用率，单位%

        Returns:
            float: 占用率
        """
        return (self.user + self.system) / self.total * 100

    def __add__(self, other):
        summary = CPUStat(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        summary.user = self.user + other.user
        summary.nice = self.nice + other.nice
        summary.system = self.system + other.system
        summary.idle = self.idle + other.idle
        summary.iowait = self.iowait + other.iowait
        summary.irq = self.irq + other.irq
        summary.softirq = self.softirq + other.softirq
        summary.stealstolen = self.stealstolen + other.stealstolen
        summary.guest = self.guest + other.guest
        summary.guest_nice = self.guest_nice + other.guest_nice

        return summary

    def __sub__(self, other):
        result = CPUStat(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        result.user = self.user - other.user
        result.nice = self.nice - other.nice
        result.system = self.system - other.system
        result.idle = self.idle - other.idle
        result.iowait = self.iowait - other.iowait
        result.irq = self.irq - other.irq
        result.softirq = self.softirq - other.softirq
        result.stealstolen = self.stealstolen - other.stealstolen
        result.guest = self.guest - other.guest
        result.guest_nice = self.guest_nice - other.guest_nice

        return result

    def __str__(self):
        attrs = vars(self)
        return ", ".join("%s: %s" % item for item in attrs.items())


@dataclass_json
@dataclass
class ProcessCPUStat:
    def __init__(self, name: str = "", utime: int = 0, stime: int = 0):
        self.name = name
        self.utime = utime
        self.stime = stime

    def __add__(self, other):
        summary = ProcessCPUStat(self.name, 0, 0)
        summary.utime = self.utime + other.utime
        summary.stime = self.stime + other.stime
        return summary

    def __sub__(self, other):
        result = ProcessCPUStat(self.name, 0, 0)
        result.utime = self.utime - other.utime
        result.stime = self.stime - other.stime
        return result

    def __str__(self):
        attrs = vars(self)
        return ", ".join("%s: %s" % item for item in attrs.items())

    @property
    def total(self) -> float:
        return self.utime + self.stime


class CPUPlugin(Plugin):
    def __init__(self, device: "Device") -> None:
        super().__init__(device)

    @property
    async def count(self) -> int:
        """
        获取cpu核心数

        因为核心数是一定的，为了避免每次都重复请求做了缓存

        Returns:
            int: 核心数
        """
        result = await self._device.shell("ls /sys/devices/system/cpu")
        match = re.findall(r"cpu[0-9+]", result)
        _cpu_count = len(match)
        return _cpu_count

    @property
    async def freqs(self) -> dict[int, CPUFreq]:
        """
        获取所有cpu的 最小最大和当前频率

        单位是Hz

        Returns:
            dict[int,CPUFreq]: key是CPU编号，value是CPUFreq
        """
        count = await self.count
        freq = {}
        for index in range(count):
            cmd_root = f"cat /sys/devices/system/cpu/cpu{index}/cpufreq"
            min = self._device.shell(f"{cmd_root}/cpuinfo_min_freq")
            cur = self._device.shell(f"{cmd_root}/scaling_cur_freq")
            max = self._device.shell(f"{cmd_root}/cpuinfo_max_freq")

            min, cur, max = await asyncio.gather(min, cur, max)

            freq[index] = CPUFreq(int(min), int(cur), int(max))

        return freq

    @property
    async def normalize_factor(self) -> float:
        """
        cpu占用标准化因子，用这个因子去乘以cpu占用率就可以得到设备无关的标准化占用率

        # 标准化因子，标准化CPU占用
        # 参考：https://blog.gamebench.net/measuring-cpu-usage-in-mobile-devices

        Returns:
            float: 因子
        """
        cpu_freqs = await self.freqs

        # 合计所有CPU最大频率
        total_max_freq = sum([v.max for _, v in cpu_freqs.items()])

        # 找出所有在在线的CPU
        online_cmd = "cat /sys/devices/system/cpu/online"
        online = await self._device.shell(online_cmd)
        phases = [
            list(map(lambda v: int(v), sub))
            for sub in [p.split("-") for p in online.split(",")]
        ]

        # 合计所有在线CPU的当前频率
        cur_freq_sum = 0
        for p in phases:
            for i in range(p[0], p[1] + 1):
                cur_freq_sum += cpu_freqs[i].cur

        return cur_freq_sum / total_max_freq

    @property
    async def cpu_stats(self) -> CPUStatMap:
        """
        通过解析/proc/stat获取每个核心的统计数据

        Returns:
            CPUStatMap: key是核心号，value是CPUStat
        """
        pattern = re.compile(
            r"cpu(\d)\s+([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s"
        )
        cpu_state_info = await self._device.shell("cat /proc/stat")
        matches = pattern.findall(cpu_state_info)

        all_cpu_state = {
            int(group[0]): CPUStat(*map(lambda x: int(x), group[1:]))
            for group in matches
        }
        return all_cpu_state

    @property
    async def cpu_usages(self) -> CPUUsageMap:
        """
        获取每个核心cpu使用率

        获取的是两次采样间隔的cpu使用率，第一获取到的永远是0，你需要再调用一次才能获取到使用率。

        Returns:
            CPUUsageMap: _description_
        """
        normalize_factor = await self.normalize_factor
        cpu_count = await self.count
        cpu_usage = {i: CPUUsage() for i in range(cpu_count)}

        last_cpu_stats = await self.cpu_stats
        await asyncio.sleep(1)
        cpu_stats = await self.cpu_stats
        for index, stat in cpu_stats.items():
            last_cpu_stat = last_cpu_stats[index]
            cpu_diff: CPUStat = stat - last_cpu_stat
            usage = round(cpu_diff.usage, 2)
            normalized = usage * normalize_factor
            cpu_usage[index] = CPUUsage(usage, normalized)
        return cpu_usage

    @property
    async def total_cpu_stat(self) -> CPUStat:
        """
        _summary_

        _extended_summary_

        Returns:
            Optional[CPUStat]: _description_
        """
        pattern = re.compile(
            r"cpu\s+([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s([\d]+)\s"
        )

        result = await self._device.shell("cat /proc/stat")
        match = pattern.search(result)

        cpu_stat = CPUStat()
        if match is None or len(match.groups()) != 10:
            raise RuntimeError("无法从 /proc/stat 中获取cpu统计")
        else:
            cpu_stat = CPUStat(*map(lambda x: int(x), match.groups()))

        return cpu_stat

    @property
    async def total_cpu_usage(self) -> CPUUsage:
        """
        获取总cpu占用率
         获取的是两次采样间隔的cpu使用率，第一获取到的永远是0，你需要再调用一次才能获取到使用率。

        Args:
            normalized (bool, optional): 标准化占有率. Defaults to False.

        Returns:
            CPUUsage: CPU使用率
        """

        last_total_cpu_stat = await self.total_cpu_stat

        # 用sleep来间隔采样
        await asyncio.sleep(1)

        total_cpu_stat = await self.total_cpu_stat
        diff = total_cpu_stat - last_total_cpu_stat

        usage = round(diff.usage, 2)
        normalize_factor = await self.normalize_factor
        normalized = usage * normalize_factor

        return CPUUsage(usage, normalized)

    async def get_pid_cpu_stat(self, pid: int) -> ProcessCPUStat:
        """以pid获取进程cpu状态

        Args:
            pid (int): 进程pid

        Returns:
            ProcessCPUStat: 进程cpu状态
        """
        result = await self._device.shell(f"cat /proc/{pid}/stat")

        if "No such file or directory" in result:
            return ProcessCPUStat("", 0, 0)
        else:
            items = result.split()
            return ProcessCPUStat(items[1], int(items[13]), int(items[14]))

    async def get_pid_cpu_usage(self, pid: int) -> CPUUsage:
        normalize_factor = await self.normalize_factor
        
        last_pid_cpu_stat = await self.get_pid_cpu_stat(pid)
        last_total_cpu_stat = await self.total_cpu_stat
        await asyncio.sleep(1)
        pid_stat = await self.get_pid_cpu_stat(pid)
        pid_diff = pid_stat - last_pid_cpu_stat

        total_cpu_stat = await self.total_cpu_stat
        cpu_diff = total_cpu_stat - last_total_cpu_stat

        app_cpu_usage = pid_diff.total / cpu_diff.total * 100
        app_cpu_usage = round(app_cpu_usage, 2)

        normalized = app_cpu_usage * normalize_factor
        return CPUUsage(app_cpu_usage, normalized)

    @property
    async def cpu_name(self) -> str:
        """
        获取CPU名

        Returns:
            str: 名字
        """
        try:
            text: str = await self._device.shell("cat /proc/cpuinfo|grep Hardware")
            name = text.split(":")[1].lstrip()
        except Exception:
            return "Unknow"
        return name

    @property
    async def info(self) -> CPUInfo:
        props = await self._device.properties

        platform = props.get("ro.board.platform", "Unknow")
        cpu_name = await self.cpu_name
        abi = props.get("ro.product.cpu.abi", "Unknow")
        core = await self.count
        freqs = await self.freqs
        freq = freqs[0]

        cpu_info = CPUInfo(platform, cpu_name, abi, core, freq)
        return cpu_info
