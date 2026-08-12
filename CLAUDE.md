# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read these first

- `CONTEXT.md` — the domain glossary. Terms have canonical forms here (character vs other player, probing vs calibration, and the four distinct kinds of "no progress"). Use them exactly; the `_Avoid_` lists exist because these words were previously overloaded.
- `docs/adr/` — decisions that should not be re-litigated. ADR 0002 in particular constrains how `decide()` may be written.

## Commands

```bash
python -m pytest -q                              # full suite (589 tests, runs offline)
python -m pytest tests/test_fsm.py -q            # one file
python -m pytest tests/test_fsm.py::test_name -q # one test
python -m pytest -k "outline or teacher" -q      # by name
```

CI installs `numpy opencv-python-headless PyYAML pytest mss` and runs `pytest -q`. There is no linter or type checker configured.

```bash
python tools/doctor.py                           # preflight: packages, config, ROIs, profile, game window
python tools/calibrate.py --write                # frame the regions, write into config/local.yaml
python tools/debug_view.py --snapshot check.png  # what the detector currently sees
python main.py --profile config/profiles/example.yaml --dry-run   # decisions only, no keystrokes
python main.py --source shot.png --max-ticks 20  # drive the whole loop from a static image
python gui.py                                    # control-panel GUI
```

`--source` and `--dry-run` are how you exercise the real pipeline without the game running.

## Architecture

One tick is `capture → perceive → decide → execute`, and each arrow is a hard boundary:

- **`capture.py`** grabs one full frame per tick. Everything downstream slices that array; nothing else touches the screen.
- **`perception.py`** (`Perceiver`) turns the frame into a `GameState` (`brain/state.py`). Regions come from `cfg.regions`; a region that falls outside the frame leaves its field `None` rather than raising.
- **`brain/fsm.py`** (`decide`) is a **pure function** — no keyboard, no screen, no clock. `now` and `playfield_center` are passed in, and cross-tick memory lives in the `Runtime` dataclass held by the caller. This is why the whole hunting policy is unit-testable; keep it that way (ADR 0002).
- **`executor.py`** turns the returned `Action` into keystrokes and writes cooldown timestamps back into the same `Runtime`. `decide` and `execute` must therefore be handed the **same** `now`.
- **`runner.py`** owns the loop, safety and watchdogs.

### The bot clock

`runner.py` keeps two clocks. Timers that ask "how long has this condition persisted" (low HP, exp stall, lost player, stuck) must use `self._bot_clock(wall)`, which excludes time the loop skipped while paused or on a black screen. Timers that ask "how much real time has passed" (`max_runtime_minutes`, performance sampling, report charts) use the wall clock. Mixing these up caused a bug where resuming from a pause instantly tripped conditions that are supposed to require sustained failure — see `_go_offline` / `_back_online`.

### Pluggable detectors

`vision/mobs.py:make_detector` returns one of four implementations behind the `MobDetector` interface — `outline` (default, zero setup, finds mobs by their black sprite outline), `template`, `yolo`, `remote`. The decision layer never knows which is in use. Add a detector by implementing `detect(img) -> List[Mob]`, not by branching in `perception.py`.

### Two config layers

- **`AppCfg`** (`config.py`, from `config/default.yaml` + gitignored `config/local.yaml`) is bound to *this machine and window*: region coordinates, vision thresholds, safety limits.
- **`Profile`** (`config/profiles/*.yaml`) is bound to *this character and map*: patrol waypoints, skills, potions, buffs.

`local.yaml` overrides `default.yaml` and is where calibration output belongs.

### The 790px reference width

Every pixel threshold in config — attack range, outline area, nametag offset, chase distance — is expressed against a 790px-wide playfield and rescaled at runtime (`REFERENCE_WIDTH`). `range_px: 320` running on a 2560-wide window is not a bug. When adding a pixel threshold, decide whether it scales and say so in a comment (ADR 0003).

### Auto-labeling (YOLO route)

Two entry points, deliberately kept separate:

- `tools/auto_pipeline.py` → `dataset.autolabel_dir` with a teacher object from `teachers.py`. Config-driven; the outline teacher builds its detector through the same `make_detector` the bot runs, so the teacher's boxes match what the bot actually sees.
- `tools/autolabel_outline.py` → `dataset.autolabel_outline_dir`. Explicit parameters, unions the outline and template teachers by IoU (their blind spots are complementary), supports `--exclude` for fixed HUD rectangles.

## Conventions

- Comments and commit messages in this repo explain **why**, usually with the measurement that motivated the change. Match that; a comment restating the code is worse than none.
- Tests validate against both synthetic images and `tests/fixtures/mapleaga_800x600.jpg`. The fixture's HP/MP/EXP ROIs are calibration data — `test_status.py` asserts against them, so do not re-encode that image without checking the readings still come out `1.0 / 1.0 / 0.591304`.
- The bot reads the screen and sends synthetic input only. It does not read game memory or touch packets, and that is a deliberate constraint, not a gap (ADR 0001).
