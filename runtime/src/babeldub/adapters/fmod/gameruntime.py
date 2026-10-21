"""Check a rebuilt bank with the game's own FMOD Studio runtime (opt-in: `verify --game-runtime`).

This is the only place where BabelDub touches game code, and only on request: the game's
fmodstudio library is loaded with ctypes in a *child process* (a crash cannot take the CLI down),
banks are loaded with NOSOUND output, nothing is played and the game executable is never started.
The rebuilt bank is checked from a copy in a temp folder.

Per probe: Studio::System::getSoundInfo(key) must succeed and point at the expected subsound,
the subsound opened as a stream (Core createSound + getSubSound) must report the expected length,
and its PCM (Sound::readData) must equal BabelDub's own decoder output bit for bit.

Run as a module: python -m babeldub.adapters.fmod.gameruntime <spec.json>  (prints one JSON line)
"""

from __future__ import annotations

import ctypes as C
import json
import os
import struct
import subprocess
import sys
import uuid
from array import array
from pathlib import Path
from typing import Any

_LIB_NAMES = ("fmodstudio.dll", "fmodstudio64.dll", "libfmodstudio.so", "libfmodstudio.dylib")
_OUTPUT_NOSOUND = 2
_MODE_LOOP_OFF = 0x1
_MODE_LOOPS = 0x2 | 0x4  # FMOD_LOOP_NORMAL | FMOD_LOOP_BIDI
_MODE_CREATESTREAM = 0x80
_MODE_COMPRESSED = 0x200
_MODE_OPENONLY = 0x2000
_TIMEUNIT_PCM = 2
_FORMAT_PCM16 = 2
_FORMAT_PCMFLOAT = 5
LOADED = 3  # FMOD_STUDIO_LOADING_STATE_LOADED
_ERR_EOF = (16, 17)  # FMOD_ERR_FILE_EOF, FMOD_ERR_FILE_ENDOFDATA


# ----------------------------------------------------------------------------- parent side
def _pe_machine(path: Path) -> int | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(0x400)
        if head[:2] != b"MZ":
            return None
        (pe,) = struct.unpack_from("<I", head, 0x3C)
        with path.open("rb") as fh:
            fh.seek(pe)
            sig = fh.read(6)
        return struct.unpack_from("<H", sig, 4)[0] if sig[:4] == b"PE\0\0" else None
    except (OSError, struct.error):
        return None


def find_fmod_library(game_dir: Path) -> Path | None:
    """The game's FMOD Studio runtime matching this Python's bitness (logging builds last)."""
    want = 0x8664 if sys.maxsize > 2**32 else 0x14C
    found: list[Path] = []
    for root, _dirs, files in os.walk(game_dir):
        for name in files:
            low = name.lower()
            if low in _LIB_NAMES or (low.startswith("libfmodstudio.so") and "l." not in low):
                found.append(Path(root) / name)
    found.sort(key=lambda p: (("x86_64" not in str(p).lower() and "64" not in p.name), str(p)))
    for path in found:
        if path.suffix.lower() == ".dll" and _pe_machine(path) != want:
            continue
        return path
    return None


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    # venv launchers on Windows start the real interpreter as a child: kill the whole tree
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    proc.kill()


def run_check(spec: dict[str, Any], tmp: Path, timeout: float = 300.0) -> dict[str, Any]:
    spec_path = tmp / f"gameruntime_{uuid.uuid4().hex[:8]}.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, "-X", "utf8", "-m", "babeldub.adapters.fmod.gameruntime", str(spec_path)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        return {"ok": False, "problems": [f"game runtime check timed out after {timeout:.0f}s"]}
    lines = [ln for ln in (out or "").splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines:
        tail = (err or out or "").strip().splitlines()[-3:]
        return {"ok": False, "problems": [f"game runtime process failed (code {proc.returncode})", *tail]}
    return json.loads(lines[-1])


# ----------------------------------------------------------------------------- child side
class _Fmod:
    def __init__(self, lib: Path) -> None:
        if hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(str(lib.parent))
        self.lib = C.CDLL(str(lib))
        vp, ui, ip = C.c_void_p, C.c_uint, C.c_int
        pvp, pui, pip = C.POINTER(C.c_void_p), C.POINTER(C.c_uint), C.POINTER(C.c_int)
        sigs: dict[str, tuple[Any, ...]] = {
            "FMOD_Studio_System_Create": (pvp, ui),
            "FMOD_Studio_System_GetCoreSystem": (vp, pvp),
            "FMOD_Studio_System_Initialize": (vp, ip, ui, ui, vp),
            "FMOD_Studio_System_LoadBankFile": (vp, C.c_char_p, ui, pvp),
            "FMOD_Studio_System_GetSoundInfo": (vp, C.c_char_p, vp),
            "FMOD_Studio_System_Update": (vp,),
            "FMOD_Studio_System_Release": (vp,),
            "FMOD_Studio_Bank_GetLoadingState": (vp, pip),
            "FMOD_Studio_Bank_LoadSampleData": (vp,),
            "FMOD_Studio_Bank_GetSampleLoadingState": (vp, pip),
            "FMOD_Studio_System_FlushSampleLoading": (vp,),
            "FMOD_System_SetOutput": (vp, ip),
            "FMOD_System_CreateSound": (vp, vp, ui, vp, pvp),
            "FMOD_Sound_GetSubSound": (vp, ip, pvp),
            "FMOD_Sound_GetFormat": (vp, pip, pip, pip, pip),
            "FMOD_Sound_GetLength": (vp, pui, ui),
            "FMOD_Sound_GetDefaults": (vp, C.POINTER(C.c_float), pip),
            "FMOD_Sound_GetName": (vp, C.c_char_p, ip),
            "FMOD_Sound_SeekData": (vp, ui),
            "FMOD_Sound_ReadData": (vp, vp, ui, pui),
            "FMOD_Sound_Release": (vp,),
        }
        for name, args in sigs.items():
            fn = getattr(self.lib, name)
            fn.argtypes = list(args)
            fn.restype = C.c_int
            setattr(self, name.removeprefix("FMOD_"), fn)


def _file_version(lib: Path) -> tuple[int, int, int, int] | None:
    """Windows version resource of the library (FMOD 2.03.06 ships as 0.2.3.6)."""
    if sys.platform != "win32":
        return None
    try:
        ver = C.windll.version  # type: ignore[attr-defined]
        size = ver.GetFileVersionInfoSizeW(str(lib), None)
        if not size:
            return None
        buf = C.create_string_buffer(size)
        ver.GetFileVersionInfoW(str(lib), 0, size, buf)
        ptr, ln = C.c_void_p(), C.c_uint()
        if not ver.VerQueryValueW(buf, "\\", C.byref(ptr), C.byref(ln)):
            return None
        ms, ls = struct.unpack_from("<II", C.string_at(ptr.value, ln.value), 8)
        return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF
    except (AttributeError, OSError, struct.error):
        return None


def _header_versions(lib: Path) -> list[int]:
    """Candidate FMOD header versions: from the DLL version resource, then a bounded scan."""
    out: list[int] = []
    fv = _file_version(lib)
    if fv:
        a, b, c, d = fv
        for major, minor, patch in ((b, c, d), (a, b, c)):
            if 1 <= major <= 3 and minor < 256 and patch < 256:
                out.append((major << 16) | (minor << 8) | patch)
    for major in (2, 1):
        for minor in range(0, 8):
            for patch in range(0, 40):
                v = (major << 16) | (minor << 8) | patch
                if v not in out:
                    out.append(v)
    return out


def _child(spec: dict[str, Any]) -> dict[str, Any]:
    lib = Path(spec["dll"])
    fm = _Fmod(lib)
    problems: list[str] = []
    sysp = C.c_void_p()
    version = None
    for v in _header_versions(lib):
        if fm.Studio_System_Create(C.byref(sysp), v) == 0:
            version = v
            break
    if version is None:
        return {"ok": False, "problems": ["FMOD_Studio_System_Create failed for every header version"]}
    core = C.c_void_p()
    fm.Studio_System_GetCoreSystem(sysp, C.byref(core))
    fm.System_SetOutput(core, _OUTPUT_NOSOUND)
    r = fm.Studio_System_Initialize(sysp, 256, 0, 0, None)
    if r != 0:
        return {"ok": False, "problems": [f"Studio::System::initialize -> FMOD_RESULT {r}"]}
    loaded: dict[str, Any] = {}
    target_bank = C.c_void_p()
    for path in [*spec.get("preload", []), spec["bank"]]:
        bank = C.c_void_p()
        r = fm.Studio_System_LoadBankFile(sysp, str(path).encode("utf-8"), 0, C.byref(bank))
        state = C.c_int(-1)
        if r == 0:
            fm.Studio_Bank_GetLoadingState(bank, C.byref(state))
        loaded[Path(path).name] = {"result": r, "state": state.value}
        if path == spec["bank"]:
            target_bank = bank
    target = loaded[Path(spec["bank"]).name]
    if target["result"] != 0 or target["state"] != LOADED:
        problems.append(f"loadBankFile(rebuilt bank) -> result {target['result']}, state {target['state']}")
    fm.Studio_System_Update(sysp)

    def sound_info(key: str) -> tuple[int, bytes]:
        info = (C.c_ubyte * 1024)()
        r = fm.Studio_System_GetSoundInfo(sysp, key.encode("utf-8"), C.cast(info, C.c_void_p))
        return r, info

    key_stats: dict[str, int] = {}
    for key in spec.get("count_keys", []):
        r, _ = sound_info(key)
        key_stats[str(r)] = key_stats.get(str(r), 0) + 1
    probes: list[dict[str, Any]] = []
    for probe in spec.get("probes", []):
        row: dict[str, Any] = {"index": probe["index"], "name": probe.get("name")}
        probes.append(row)
        info = None
        for key in probe["keys"]:
            r, buf = sound_info(key)
            if r == 0:
                row["key"] = key
                info = buf
                break
            row["key_result"] = r
        if info is None:
            if probe.get("required", True):
                problems.append(f"getSoundInfo failed for subsound {probe['index']} ({probe.get('name')})")
            continue
        raw = bytes(info)
        name_or_data, mode = struct.unpack_from("<QI", raw, 0)
        (cbsize,) = struct.unpack_from("<i", raw, 16)
        # index inside the sound FMOD opens: it restricts the FSB to the requested subsound, so this
        # is usually 0; the subsound is identified by its name and PCM below
        (subindex,) = struct.unpack_from("<i", raw, 16 + cbsize)
        row["subsound"] = subindex
        snd = C.c_void_p()
        smode = (
            (mode & ~(_MODE_COMPRESSED | _MODE_LOOPS)) | _MODE_LOOP_OFF | _MODE_CREATESTREAM | _MODE_OPENONLY
        )
        exinfo = C.c_void_p(C.addressof(info) + 16)
        r = fm.System_CreateSound(core, C.c_void_p(name_or_data), smode, exinfo, C.byref(snd))
        if r != 0:
            problems.append(f"{row['key']}: createSound -> FMOD_RESULT {r}")
            continue
        try:
            sub = C.c_void_p()
            r = fm.Sound_GetSubSound(snd, subindex, C.byref(sub))
            if r != 0:
                problems.append(f"{row['key']}: getSubSound -> FMOD_RESULT {r}")
                continue
            typ, fmt, ch, bits = C.c_int(), C.c_int(), C.c_int(), C.c_int()
            fm.Sound_GetFormat(sub, C.byref(typ), C.byref(fmt), C.byref(ch), C.byref(bits))
            length = C.c_uint()
            fm.Sound_GetLength(sub, C.byref(length), _TIMEUNIT_PCM)
            freq, prio = C.c_float(), C.c_int()
            fm.Sound_GetDefaults(sub, C.byref(freq), C.byref(prio))
            name = C.create_string_buffer(512)
            fm.Sound_GetName(sub, name, 512)
            row.update(
                type=typ.value,
                format=fmt.value,
                channels=ch.value,
                rate=round(freq.value),
                length=length.value,
                fmod_name=name.value.decode("utf-8", "replace"),
            )
            if probe.get("name") and row["fmod_name"] != probe["name"]:
                problems.append(f"{row['key']}: FMOD opened '{row['fmod_name']}', expected '{probe['name']}'")
            if length.value != probe["nsamples"]:
                problems.append(f"{row['key']}: FMOD length {length.value} != {probe['nsamples']} samples")
            if ch.value != probe["channels"] or round(freq.value) != probe["rate"]:
                problems.append(f"{row['key']}: FMOD format {ch.value}ch/{freq.value:.0f}Hz differs")
            if probe.get("expect"):
                expect = Path(probe["expect"]).read_bytes()
                # never read past the reported length: a stream may wrap around instead of ending
                want = length.value * max(1, ch.value) * max(2, bits.value // 8)
                got = bytearray()
                fm.Sound_SeekData(sub, 0)
                block = (C.c_ubyte * (1 << 20))()
                while len(got) < want:
                    n = C.c_uint()
                    r = fm.Sound_ReadData(sub, block, min(len(block), want - len(got)), C.byref(n))
                    got += bytes(block[: n.value])
                    if r != 0 or n.value == 0:
                        break
                if fmt.value == _FORMAT_PCMFLOAT:
                    floats = struct.unpack(f"<{len(got) // 4}f", bytes(got))
                    got = bytearray(
                        b"".join(struct.pack("<h", max(-32768, min(32767, round(f * 32768)))) for f in floats)
                    )
                elif fmt.value != _FORMAT_PCM16:
                    row["exact"] = None
                    continue
                n_cmp = min(len(got), len(expect)) // 2
                if bytes(got) == expect:
                    diff = 0
                else:
                    a = array("h", bytes(got[: 2 * n_cmp]))
                    b = array("h", expect[: 2 * n_cmp])
                    diff = sum(1 for x, y in zip(a, b, strict=True) if x != y)
                row["exact"] = diff == 0 and len(got) == len(expect)
                row["differing_samples"] = diff
                row["read_bytes"] = len(got)
                if not row["exact"]:
                    problems.append(
                        f"{row['key']}: FMOD PCM differs from BabelDub decoder "
                        f"({diff} samples, {len(got)} vs {len(expect)} bytes)"
                    )
        finally:
            fm.Sound_Release(snd)
    sample_state = None
    if target_bank.value and target["result"] == 0:
        # non-streamed sample data of every event in the bank (regular waveform instruments)
        r = fm.Studio_Bank_LoadSampleData(target_bank)
        fm.Studio_System_FlushSampleLoading(sysp)
        state = C.c_int(-1)
        fm.Studio_Bank_GetSampleLoadingState(target_bank, C.byref(state))
        sample_state = state.value if r == 0 else f"error {r}"
    fm.Studio_System_Release(sysp)
    fv = _file_version(lib)
    return {
        "ok": not problems,
        "fmod_dll_version": (f"{fv[1]}.{fv[2]:02d}.{fv[3]:02d}" if fv[0] == 0 else ".".join(map(str, fv)))
        if fv
        else None,
        "fmod_header_version": f"{version >> 16}.{(version >> 8) & 0xFF:02d}.{version & 0xFF:02d}",
        "sample_data": sample_state,
        "loaded": loaded,
        "keys": key_stats,
        "probes": probes,
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    try:
        result = _child(spec)
    except (OSError, AttributeError) as exc:
        result = {"ok": False, "problems": [f"cannot use the game's FMOD library: {exc}"]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
