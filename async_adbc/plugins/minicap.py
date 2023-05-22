import os
import pkg_resources
from . import Plugin

MINICAP_LIBS = pkg_resources.resource_filename("async_adbc", "vendor/minicap")


class MinicapPlugin(Plugin):
    PUSH_TO = "/data/local/tmp"

    async def init(self):
        """
        初始化minicap
        """

        exists = await self._device.file_exists("/data/local/tmp/minicap")
        if exists:
            return

        props = await self._device.properties
        abi = props.get("ro.product.cpu.abi")
        pre_sdk = props.get("ro.build.version.preview_sdk")
        rel_sdk = props.get("ro.build.version.release")
        sdk = props.get("ro.build.version.sdk")
        sdk = int(sdk)

        if pre_sdk.isdigit() and int(pre_sdk) > 0:
            sdk += 1

        if sdk >= 16:
            binfile = "minicap"
        else:
            binfile = "minicap-nopie"

        binfile_path = os.path.join(MINICAP_LIBS, abi, binfile)
        await self._device.push(binfile_path, self.PUSH_TO + "/minicap", chmode=0o755)

        sofile_path = os.path.join(
            MINICAP_LIBS, f"minicap-shared/aosp/libs/android-{sdk}/{abi}/minicap.so"
        )

        if not os.path.isfile(sofile_path):
            sofile_path = os.path.join(
                MINICAP_LIBS,
                f"minicap-shared/aosp/libs/android-{rel_sdk}/{abi}/minicap.so",
            )

        await self._device.push(sofile_path, self.PUSH_TO + "/minicap.so", chmode=0o755)

    async def get_frame(self):
        self.init()

        resolution = await self._device.wm.size()
        size = resolution.override_size
        orientation = await self._device.wm.orientation()
        raw_data = await self._device.shell_raw(
            "LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap",
            "-P",
            f"{size}@{size}/{orientation}",
            "-s",
        )

        jpg_data = raw_data.split(b"for JPG encoder\n")[-1]
        return jpg_data
