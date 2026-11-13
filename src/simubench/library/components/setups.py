"""An instrument's setup memory (#1070): ``*SAV``/``*RCL`` registers, state files and the learn
string — the instrument's settings saved and put back.

A setup is every setting a command can change (``Instrument.settings_snapshot``: no read-only
variable, no calibration coefficient, no drawn individual value), as a small JSON document; the
same SimuBench reads back what it wrote, the vendor's own byte format is not reproduced (the
coverage table says so). Registers survive ``*RST`` and, like the instrument's files, the next run
of a saved bench: they are kept in the instrument's folder, or as files on one of its drives when
the definition names them (the Trueform keeps ``*SAV n`` in ``INT:\\Settings\\STATE_n.STA``).
"""

from __future__ import annotations

import json
import math
import re
from fnmatch import fnmatchcase
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

FORMAT = "simubench.setup/1"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _plain(value: Any) -> Any:
    """A setting as JSON keeps it (infinity, a high-Z load, as the string 'inf')."""
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def _back(value: Any) -> Any:
    return float(value) if value in ("inf", "-inf") else value


def encode_setup(model: str, settings: dict[str, Any]) -> bytes:
    """A setup as the bytes a register or state file holds."""
    doc = {"format": FORMAT, "model": model, "settings": {k: _plain(v) for k, v in settings.items()}}
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def decode_setup(data: bytes, model: str) -> dict[str, Any] | None:
    """The settings of a setup this model wrote, or None (another format, another model)."""
    try:
        doc = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("model") != model:
        return None
    settings = doc.get("settings")
    return {k: _back(v) for k, v in settings.items()} if isinstance(settings, dict) else None


@component("setup_memory")
class SetupMemory(Component):
    """Save and recall the instrument's settings: registers (``*SAV``/``*RCL``,
    ``:STORe:SETup``), named state files on the instrument's drives (``MMEMory:STORe:STATe``) and
    the setup as a block (``*LRN?``, ``:SYSTem:SETup``).

    ``registers`` numbered from ``first``; ``register_file`` (``INT:\\Settings\\STATE_{n}.STA``,
    needs ``storage``) keeps each register as a file the MMEMory commands see, else they are kept
    beside the instrument's drives. ``exclude`` lists settings a setup leaves out (glob patterns
    on the state path). Recalling an empty register raises ``empty_error`` (0: no SCPI error, the
    instrument only says so on its screen); a file or block that is not a setup of this model
    raises ``bad_setup``. Model limits: a setup holds settings only — arbitrary waveforms, reading
    memory and other data are not in it; the power-down state (location 0 on the Trueform) is not
    written at the end of a run.
    """

    refs = {"storage": ("file_system", "instrument")}
    optional_refs = frozenset({"storage"})
    defaults = {
        "storage": "",  # the file_system the state files and register files are on (empty: none)
        "registers": 10,  # how many registers *SAV/*RCL have
        "first": 0,  # the first register's number
        "register_file": "",  # a register as a file on storage, e.g. INT:\Settings\STATE_{n}.STA (empty: kept internally)
        "names": [],  # the registers' factory names, first register first (MEMory:STATe:NAME)
        "name_length": 12,  # longest register name
        "extension": "",  # added to a state file name that has none, e.g. .sta
        "exclude": [],  # settings a setup leaves out: glob patterns on the state path
        "empty_error": -221,  # recalling an empty register (0: no SCPI error)
        "bad_setup": -230,  # a file or block that is not a setup of this model
        "learn_prefix": "",  # what *LRN? writes before the block, e.g. ':SYST:SET ' (Infiniium)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.custom_names: dict[int, str] = {}

    # ---- the setup ------------------------------------------------------------------------------

    def _model(self) -> str:
        assert self.owner is not None
        return self.owner.definition.id

    def capture(self) -> bytes:
        """The instrument's settings now, as setup bytes."""
        assert self.owner is not None
        patterns = [str(p) for p in self.param("exclude") or []]
        settings = {
            k: v
            for k, v in self.owner.settings_snapshot().items()
            if not any(fnmatchcase(k, p) for p in patterns)
        }
        return encode_setup(self._model(), settings)

    def apply(self, data: bytes) -> None:
        """Put a setup back; refused unchanged when it is not a setup of this model."""
        assert self.owner is not None
        settings = decode_setup(data, self._model())
        if settings is None:
            raise ScpiError(int(self.param("bad_setup")), "not a setup of this instrument")
        self.owner.restore_settings(settings)

    # ---- registers ------------------------------------------------------------------------------

    def _register(self, n: int) -> int:
        first, count = int(self.param("first")), int(self.param("registers"))
        if not first <= int(n) < first + count:
            raise ScpiError(-222, f"register {n} is not {first}..{first + count - 1}")
        return int(n)

    def _storage(self) -> Any:  # the file_system, or None
        return self.ref("storage")

    def _internal(self, n: int) -> Any:
        assert self.owner is not None
        folder = self.session.instrument_files(self.owner.name) / "_registers"
        folder.mkdir(exist_ok=True)
        return folder / f"{n}.json"

    def _register_name(self, n: int) -> str:
        template = str(self.param("register_file") or "")
        return template.replace("{n}", str(n))

    def _read_register(self, n: int) -> bytes | None:
        storage = self._storage()
        if storage is not None and self.param("register_file"):
            try:
                return storage.load(self._register_name(n))
            except ScpiError:
                return None
        path = self._internal(n)
        return path.read_bytes() if path.is_file() else None

    def _write_register(self, n: int, data: bytes | None) -> None:
        storage = self._storage()
        if storage is not None and self.param("register_file"):
            name = self._register_name(n)
            if data is None:
                place = storage.resolve(name)
                path = storage.host(place)
                if path.is_file():
                    path.unlink()
                return
            folder = storage.resolve(name)
            parent = storage.host((folder[0], folder[1][:-1]))
            parent.mkdir(parents=True, exist_ok=True)
            storage.store(name, data)
            return
        path = self._internal(n)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)

    def _default_name(self, n: int) -> str:
        names = [str(x) for x in self.param("names") or []]
        i = n - int(self.param("first"))
        return names[i] if 0 <= i < len(names) else f"STATE_{n}"

    @op("save")
    def op_save(self, register: int, **_: Any) -> OpResult:
        """Save the settings in a register (*SAV, :STORe:SETup); what it held is replaced."""
        self._write_register(self._register(register), self.capture())
        return OpResult()

    @op("recall")
    def op_recall(self, register: int, **_: Any) -> OpResult:
        """Put a register's settings back (*RCL, :RECall:SETup); an empty one raises
        ``empty_error`` and changes nothing."""
        data = self._read_register(self._register(register))
        if data is None:
            code = int(self.param("empty_error"))
            if code:
                raise ScpiError(code, f"register {register} is empty")
            return OpResult()
        self.apply(data)
        return OpResult()

    @op("valid")
    def op_valid(self, register: int, **_: Any) -> OpResult:
        """1 when the register holds a setup, else 0 (MEMory:STATe:VALid?)."""
        return OpResult(V.format_int(int(self._read_register(self._register(register)) is not None)))

    @op("delete")
    def op_delete(self, register: int, **_: Any) -> OpResult:
        """Empty a register; its name goes back to the factory name (MEMory:STATe:DELete)."""
        n = self._register(register)
        self._write_register(n, None)
        self.custom_names.pop(n, None)
        return OpResult()

    @op("name")
    def op_name(self, register: int, name: str | None = None, query: bool = False, **_: Any) -> OpResult:
        """A register's name (MEMory:STATe:NAME): a letter, then letters, digits or ``_``; none
        given restores the factory name."""
        n = self._register(register)
        if query:  # unquoted, as the Trueform answers it (TEST_RACK_1)
            return OpResult(self.custom_names.get(n, self._default_name(n)).encode())
        if not name:
            self.custom_names.pop(n, None)
            return OpResult()
        if not _NAME.match(name) or len(name) > int(self.param("name_length")):
            raise ScpiError(-224, f"{name!r} is not a register name")
        self.custom_names[n] = name
        return OpResult()

    @op("catalog")
    def op_catalog(self, **_: Any) -> OpResult:
        """Every register's name, quoted (MEMory:STATe:CATalog?)."""
        first = int(self.param("first"))
        names = [
            self.custom_names.get(n, self._default_name(n))
            for n in range(first, first + int(self.param("registers")))
        ]
        return OpResult(b",".join(V.format_string(x) for x in names))

    @op("count")
    def op_count(self, **_: Any) -> OpResult:
        """How many registers there are (MEMory:NSTates?)."""
        return OpResult(V.format_int(int(self.param("registers"))))

    # ---- state files and the learn string ---------------------------------------------------------

    def _file(self, name: str) -> str:
        ext = str(self.param("extension") or "")
        tail = re.split(r"[\\/:]", name)[-1]
        return name + ext if ext and "." not in tail else name

    @op("store_file")
    def op_store_file(self, name: str, **_: Any) -> OpResult:
        """Save the settings to a file on the instrument's drives (MMEMory:STORe:STATe)."""
        storage = self._storage()
        if storage is None:
            raise ScpiError(-300, f"{self.uid}: no storage for state files")
        storage.store(self._file(name), self.capture())
        return OpResult()

    @op("load_file")
    def op_load_file(self, name: str, others: str = "", default_extension: str = "", **_: Any) -> OpResult:
        """Put the settings of a state file back (MMEMory:LOAD:STATe). ``others``: the command also
        loads other file types (the Infiniium's :DISK:LOAD: waveforms into memories), which are not
        modeled — a name with another extension (``default_extension`` when it has none) is refused
        with -257 and this text."""
        if others:
            tail = re.split(r"[\\/:]", name)[-1]
            ext = ("." + tail.rsplit(".", 1)[-1]) if "." in tail else default_extension
            if ext.casefold() != str(self.param("extension") or "").casefold():
                raise ScpiError(-257, others)
        storage = self._storage()
        if storage is None:
            raise ScpiError(-300, f"{self.uid}: no storage for state files")
        self.apply(storage.load(self._file(name)))
        return OpResult()

    @op("learn")
    def op_learn(self, **_: Any) -> OpResult:
        """The setup as a definite-length block after ``learn_prefix`` (*LRN?): sent back as it
        is, it restores the settings."""
        return OpResult(str(self.param("learn_prefix")).encode() + V.format_block(self.capture()))

    @op("setup")
    def op_setup(self, data: bytes | None = None, query: bool = False, **_: Any) -> OpResult:
        """The setup as a block (:SYSTem:SETup?), or put one back (:SYSTem:SETup <block>)."""
        if query or data is None:
            return OpResult(V.format_block(self.capture()))
        self.apply(data)
        return OpResult()
