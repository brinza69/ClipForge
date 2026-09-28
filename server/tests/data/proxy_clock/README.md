# proxy_clock evidence

The small evidence behind `services/clipper/proxy_clock.py::VALIDATED_CLOCKS`, so a clean
checkout can inspect the probes and run the tests without `data/claude-master-*`. No media:
every source and proxy is rebuilt in a temp dir and deleted.

`.gitattributes` here is `* -text`: the repo runs `core.autocrlf=true` and `synth_probe.txt`
has mixed line endings, so without it a checkout would change the bytes and every sha256.

## Files

| file | what | origin |
|---|---|---|
| `synth_24_23.976_60_30_25_59.94.json` | the frozen M0 v2 synthetic probe: per rate, every slot of a 600 s barcode source through the proxy recipe, decoded and compared with `simulate()` / `address_slot()` | verbatim, `B/gates/m0v2/` |
| `synth_probe.txt` | that probe's printed report | verbatim, `B/gates/m0v2/` |
| `FROZEN.json` | the M0 v2 freeze: ffmpeg build (version, libs, configuration and exe sha256) and the sha256 of every frozen file | verbatim, `B/gates/m0v2/` |
| `synth_probe.py.txt`, `test_m0v2.py.txt`, `addrlib.py.txt`, `mediaio.py.txt` | the frozen harness, for reading (`.txt`: never collected, never imported) | verbatim, `B/gates/m0v2/*.py` |
| `offlib.py.txt`, `synthetic_long.py.txt` | the frozen helpers the probe imports | verbatim, `B/gates/offset/*.py` |
| `reproduce_synth.py` | reproduction with the SHIPPING code (recipe, ffmpeg_build, simulate, address_slot) | new |
| `reproduce_synth_600s.json` | its 600 s output, 6/6 rates ok | new |
| `SHA256SUMS` | sha256 of every file above | new |

Every `*.txt` copy and the synth JSON have the sha256 FROZEN.json lists for their origin
(`synth_probe.txt` is not in FROZEN.json; it equals `B/gates/m0v2/synth_probe.txt`, 198b2b64…).
`tests/test_clipper_proxy_clock_evidence.py` checks all of it.

The reproduction differs from the frozen probe in the SOURCE only: the probe drew the barcode
with lavfi `geq` at 960x540, the reproduction pipes raw frames at 480x272. Recipe, params (480 /
10 fps / GOP 10) and the rule under test are the same, and the per-rate numbers (slots 6002,
0 simulate mismatches, 0 address mismatches, the same addressed counts and refusals) are equal.

## Commands

From `server/`, with the backend's venv (Windows paths shown):

```
# the evidence tests (no ffmpeg needed)
.venv\Scripts\python.exe -m pytest tests/test_clipper_proxy_clock_evidence.py -v

# the reproduction: ~20 min for six 600 s rates; exit 0 only when every rate was read and matched
.venv\Scripts\python.exe tests/data/proxy_clock/reproduce_synth.py --seconds 600 --out tests/data/proxy_clock/reproduce_synth_600s.json

# a quick smoke run
.venv\Scripts\python.exe tests/data/proxy_clock/reproduce_synth.py --seconds 20

# after regenerating anything here: rewrite SHA256SUMS, then the sha256 in VALIDATED_CLOCKS
.venv\Scripts\python.exe -c "import hashlib,pathlib as p;d=p.Path('tests/data/proxy_clock');(d/'SHA256SUMS').write_bytes(''.join(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n' for f in sorted(d.iterdir()) if f.is_file() and f.name!='SHA256SUMS').encode())"
```

On an ffmpeg build other than the validated one the reproduction still runs, but every
address is refused `clock_unvalidated` and the exit code is 1.

The frozen probe itself (`synth_probe.py.txt`) needs the rest of the M0 v2 harness and ran as
`python synth_probe.py` inside `B/gates/m0v2/`; it is kept for reading, not re-run from here.
