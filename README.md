<a id="-tunex"></a>

<div align="center">

# <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/settings.svg" alt="" width="20" height="20"> Tunex

<img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/crown.svg" alt="" width="20" height="20"> **UCI tuning and strength testing for IronPhoenix 4-player chess.**

**Tunex** is a lightweight Python tuning and strength-testing harness built for [IronPhoenix](https://github.com/KnightStackSys/IronPhoenix), a 4-player chess Teams engine.

It keeps chess computation inside the C++ engine and uses Python for the parts that benefit from rapid iteration: launching engine processes, applying UCI parameters, paired match testing, Elo statistics, SPRT, SPSA, experiment logs, and resumable tuning state.

</div>

---

<a id="-what-it-supports"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/zap.svg" alt="" width="20" height="20"> What it supports

- **UCI runtime parameters** — change IronPhoenix search/HCE values without recompiling.
- **4PC Teams match runner** — candidate plays Red+Yellow in one game and Blue+Green in the paired game.
- **Paired openings** — the same opening is reused when teams are swapped.
- **Fixed movetime, node, or depth testing**.
- **W/D/L, score, Elo, and approximate 95% confidence intervals**.
- **Sequential SPRT** for isolated search/evaluation experiments.
- **SPSA** for tuning multiple integer UCI parameters at once.
- **Parallel games** with configurable workers.
- **JSONL game logs** and **resumable SPSA state**.
- Windows paths work normally; no shell quoting is required.

Tunex currently targets IronPhoenix's 4PC UCI behavior, including its immediate king-capture terminal score (`mate 1`).

<a id="-requirements"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/wand-sparkles.svg" alt="" width="20" height="20"> SpellWarden (8×8 Spell Chess) support

TuneX supports two match variants; existing IronPhoenix configs default to
`"variant": "four_player_teams"`. SpellWarden configs use
`"variant": "spell_chess"` and the engine's native 8×8 UCI extension.

Start from `configs/spellwarden.example.json` and update `engine.path`:

~~~powershell
tunex -c configs/spellwarden.example.json probe
tunex -c configs/spellwarden.example.json match --games 20
tunex -c configs/spellwarden.example.json sprt
tunex -c configs/spellwarden.example.json spsa
~~~

The match runner alternates **White and Black**, swapping candidate and
baseline colors in paired games. Complete spell turns are single tokens:
`freeze@d5:e2e4`, `jump@d4:d1d8`, or a normal `e2e4`.
Openings may contain standard six-field chess FEN with optional SpellWarden
`sw=...` metadata, or move lists beginning with `moves`.
Opening turns are validated through the engine's exhaustive `legalmoves`
command. SAN is not yet supported for Spell Chess.

For accurate checkmate/stalemate adjudication, use SpellWarden with the
integration update to its `d` diagnostic, which exposes
`info string check: yes/no` alongside its existing FEN. SpellWarden
reports its own spell-aware check status; TuneX must not approximate it with
orthodox chess attack rules. King captures are recognized from the returned
8×8 board FEN; max-plies are draws. The engine's existing
`SpellTargets`, `RootSpellTargets`, and `UseMoveOrdering` options can be
used in matches and SPSA. **There is no automatic Spell Chess rating oracle**:
test results reflect SpellWarden's current rules implementation.

IronPhoenix four-player behavior, FEN4/SAN4 parsing, team pairing, and all
existing configs remain unchanged when `variant` is omitted.

## Bayesian optimization (TPE)

TuneX supports a dependency-free **Tree-structured Parzen Estimator (TPE)**
for model-guided optimization of discrete/integer UCI parameters. It samples
initial configurations at random and uses smoothed density estimates from the
best and worst observed trials to choose promising combinations. TPE is a
Bayesian optimization approach, not a Gaussian-process surrogate.

Copy `configs/bayesian.example.json`, adjust `engine.path` and the options
you want to tune, then run:

~~~powershell
tunex -c configs/bayesian.example.json bayesian
tunex -c configs/bayesian.example.json bayesian --iterations 150
~~~

The `bayesian` section contains `iterations`, `games_per_trial` (even),
`startup_trials`, `candidates` (proposal pool size), `gamma` (fraction
of strongest observations), `prior_weight` (noise shrinkage toward 50%),
`seed`, `state_file`, and integer `parameters` with `name`, `value`,
`min`, `max`, `step`. The existing `baseline.options` are the fixed
control settings. TuneX tests each proposed candidate against the **same
baseline executable and options**, using paired team/color swaps and the
existing match runner. The first trial uses the supplied starting values.

A completed trial is recorded with its exact parameter vector, W/D/L, and
score. Random state, all observations, and the best observed options are
saved atomically so a subsequent command resumes deterministically. A resume
with incompatible time controls, baseline, parameter ranges, seed or engine
path is rejected to avoid mixing unrelated experiments. Do not run multiple
tuners against one state file at the same time.

This is an optimizer, **not a guarantee of Elo gains**. With small per-trial
game budgets, even a model-guided tuner can select lucky configurations.
Independently verify the reported best settings against an untouched baseline
using SPRT and fresh openings. The current optimizer supports integer-valued
spin options; boolean switches are best evaluated separately. It works with
IronPhoenix and SpellWarden by selecting the corresponding `variant` and
using parameters actually advertised by that engine.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/clipboard-list.svg" alt="" width="20" height="20"> Requirements

- Python **3.11+**
- A built IronPhoenix executable
- IronPhoenix with runtime tuning UCI options (the `runtime-tuning-parameters` work in IronPhoenix)

Tunex itself has no third-party runtime dependencies.

<a id="-install"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/download.svg" alt="" width="20" height="20"> Install

From the Tunex repository:

```bash
python -m pip install -e .
```

Then:

```bash
tunex --help
```

<a id="-configure-ironphoenix"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/settings.svg" alt="" width="20" height="20"> Configure IronPhoenix

Copy `configs/ironphoenix.example.json` to `configs/ironphoenix.json` and change `engine.path` to your executable.

A typical Windows path is:

```json
{
  "engine": {
    "path": "C:/Users/you/Documents/GitHub/IronPhoenix/out/build/x64-release/ironphoenix.exe"
  }
}
```

Forward slashes are convenient in JSON even on Windows.

Recommended common options for search-parameter tuning are:

```json
"common_options": {
  "Hash": 64,
  "MultiPV": 1,
  "UseNNUE": false
}
```

Keeping NNUE off is useful when tuning HCE or search behavior against a stable HCE baseline. Turn it on deliberately when you want NNUE-backed search tests.

### Compare two different engine executables

For SPRT or a fixed match between **two different .exe builds**, give each
variant its own `path`. The paths under `baseline` and `candidate`
override `engine.path`; omit them to retain the older behavior of using one
executable with different UCI options.

```json
{
  "engine": {
    "path": "C:/Engines/IronPhoenix-old.exe",
    "common_options": {
      "Hash": 64,
      "MultiPV": 1,
      "UseNNUE": false
    }
  },
  "baseline": {
    "name": "Before change",
    "path": "C:/Engines/IronPhoenix-old.exe",
    "options": {}
  },
  "candidate": {
    "name": "After change",
    "path": "C:/Engines/IronPhoenix-new.exe",
    "options": {}
  }
}
```

Keep the other `search`, `match`, and `sprt` sections in the same config
file. Paths can be absolute (Windows forward slashes are accepted) or relative
to the config file's directory. `tunex probe` checks the candidate executable.

Recommended run from the Tunex repository root:

```powershell
python -m pip install -e .
tunex -c configs/ironphoenix.json probe
tunex -c configs/ironphoenix.json match --games 20
tunex -c configs/ironphoenix.json sprt
```

For a fair binary comparison, compile both executables in Release mode, use
the same NNUE and hash settings, fixed `search.mode = "movetime"`, and
`match.paired = true`. Each game uses a fresh candidate and baseline process,
and Tunex sends `ucinewgame` to both before the game begins.


<a id="-verify-the-engineoptions"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/search.svg" alt="" width="20" height="20"> Verify the engine/options

```bash
tunex -c configs/ironphoenix.json probe
```

Tunex performs a UCI handshake, validates configured option names, applies the options, waits for `readyok`, and asks IronPhoenix for `tuneparams`.

If a configured tuning option is not advertised, Tunex fails early instead of silently running a useless match.

<a id="-new-game-reset-and-correction-histories"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/refresh-cw.svg" alt="" width="20" height="20"> New-game reset and correction histories

Tunex sends `ucinewgame` to **both** IronPhoenix processes after applying
their UCI options and waits for `readyok` **before every game**, including
each game in a paired match, SPRT batch, or SPSA iteration. It does **not**
send `ucinewgame` between moves in the same game. This preserves IronPhoenix's
game-long pawn and continuation correction histories and clears them between
games. The engine processes are also newly created for each game.

This protocol is regression-tested for both serial and parallel match workers.

<a id="-run-a-fixed-match"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/play.svg" alt="" width="20" height="20"> Run a fixed match

```bash
tunex -c configs/ironphoenix.json match
```

Override the configured game count:

```bash
tunex -c configs/ironphoenix.json match --games 200
```

With paired testing, game 1 assigns the candidate to **Red+Yellow** and game 2 assigns it to **Blue+Green**, using the same opening.

Every completed game is appended to the configured JSONL result file.

<a id="-openings"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/book-open.svg" alt="" width="20" height="20"> Openings

Set:

```json
"openings_file": "openings.txt"
```

Each non-comment line can contain **UCI coordinates**, **SAN**, or a mix
of both. For example, the following represent the same four opening plies
in Modern four-player Teams chess (Red, Blue, Yellow, Green):

```text
moves e2e3 b7c7 g13g12 m7l7
san e3 c7 g12 l7
e3 c7 g12 l7
moves e2e3 c7 g13g12 l7
```

SAN supports ordinary piece moves such as `Nf3`, captures `Bxe5` / `hxi5`,
source disambiguation (`Nef3`), castling (`O-O` / `O-O-O`), promotions
(`g11=Q`), and trailing check or annotation symbols. Four-player
PGN-style long algebraic moves such as `Ne1-f3` are supported too.
For Chess.com four-player opening books, **each line can be an independent
opening or an increasingly long prefix**, without a `moves` header:

```text
g2-g4
g2-g4 b7-c7+
g2-g4 b7-c7+ g13-g12
g2-g4 b7-c7+ g13-g12 m8-l8
g2-g4 b7-c7+ g13-g12 m8-l8 Ne1-f3
```

The converter also recognizes captured-piece identification, such as
`Ra4xQd4`, `b6xBc7`, `Kh1xQi2`, and `Ka7xBb6`.
These examples are **long algebraic (PGN4)** rather than short SAN;
Tunex supports both and removes trailing check markers (`+`, `#`)
before passing UCI moves to IronPhoenix. The engine's legal move list
still determines whether an opening is valid.

### Use one FEN4 position per line

You can also provide a plain UTF-8 `.txt` file with **one complete native
Chess.com FEN4 per line**, without a `fen ` prefix. FEN4 lines begin with
`R-`, `B-`, `Y-`, or `G-` (the side to move). The literal `4PC`
also works for the default starting position. Blank lines and lines
beginning with `#` are skipped.

Set `match.openings_file` in your JSON configuration, for example:

```json
"match": {
  "games": 100,
  "workers": 4,
  "paired": true,
  "openings_file": "positions.txt"
}
```

File paths are relative to the **config file's directory** unless absolute.
Each game randomly selects an entry; paired games reuse the **same FEN4
position** while swapping the candidate and baseline teams. This also works
for SPRT and SPSA because all modes use the same match runner.

The original prefixed syntax and SAN/UCI move lists remain supported.
You can combine SAN with a custom Chess.com FEN4:

```text
fen <FEN4> | san Nf3 c7
fen <FEN4> | moves e1f3 c7
fen 4PC | san e3 c7
```

You can also omit `fen ` from these lines when supplying a native FEN4.
Only use complete, valid FEN4 snapshots; do not replace the fields with
placeholders such as `R-...`.

Tunex queries IronPhoenix for the **current legal 4PC moves and FEN4**
to resolve SAN into unique UCI coordinates before gameplay. Invalid,
ambiguous, or unsupported SAN aborts with the offending opening ply rather
than guessing. No ordinary 8×8 chess library is used. Conversion happens
before the game searches and does not send `ucinewgame` between moves.

Paired games reuse the **converted UCI opening** when swapping candidate and
baseline teams. SAN conversion requires an IronPhoenix build supporting the
`fen` and `moves` commands, as current IronPhoenix does.

<a id="-sprt"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/gauge.svg" alt="" width="20" height="20"> SPRT

Use SPRT for questions such as: *Does this LMP change gain at least a small amount of Elo?*

Configure:

```json
"sprt": {
  "elo0": 0,
  "elo1": 5,
  "alpha": 0.05,
  "beta": 0.05,
  "max_games": 10000,
  "batch_games": 20
}
```

Then run:

```bash
tunex -c configs/ironphoenix.json sprt
```

Tunex immediately prints the two engine paths and search settings, then
shows when each game starts and as soon as each game finishes, without
printing repeated per-ply `Game ... playing | ply ...` messages.
In multi-worker tests, start and completion lines may be interleaved; each
includes the game number. Game counters and W/D/L are updated after each
completed game; SPRT LLR, hypothesis boundaries, Elo estimates and decisions
are evaluated at **completed batch boundaries**. Fixed matches retain
their existing per-ply progress messages.

Progress output is unbuffered (`flush=True`). For older installs, run
`git pull` and `python -m pip install -e .` before rerunning Tunex.
The `match.games` setting controls fixed matches only; SPRT uses
`sprt.max_games`. With `batch_games=2` and `workers=4`, at most two
games will be active during each batch.

<a id="-sprt-model-note"></a>

### <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/calculator.svg" alt="" width="20" height="20"> SPRT model note

The first Tunex release uses a **logistic-score SPRT**. Draws count as half a win and half a loss. This is intentionally simple and useful for development, but it is not a pentanomial paired-game SPRT. The raw W/D/L and Elo output are always shown so you can inspect the actual result.

<a id="-spsa"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/flask-conical.svg" alt="" width="20" height="20"> SPSA

SPSA is useful for tuning several search constants simultaneously.

Example parameters:

```json
"parameters": [
  {
    "name": "SearchSEEPruneMargin",
    "value": 80,
    "min": 20,
    "max": 200,
    "step": 5
  },
  {
    "name": "SearchLMRBaseX100",
    "value": 75,
    "min": 25,
    "max": 150,
    "step": 5
  }
]
```

Run:

```bash
tunex -c configs/ironphoenix.json spsa
```

Or stop at a particular total iteration:

```bash
tunex -c configs/ironphoenix.json spsa --iterations 25
```

State is written after every iteration. Restarting the same command resumes from that state file instead of throwing away the tuning run.

SPSA creates a `+` and `-` parameter vector each iteration, runs them directly against one another in paired games, estimates a gradient, and moves the center parameter vector.

<a id="-parallelism"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/cpu.svg" alt="" width="20" height="20"> Parallelism

`match.workers` controls simultaneous games. Each game uses **two IronPhoenix processes**, so `workers = 4` can use up to eight engine processes at once.

Start conservatively. For stable Elo testing, avoiding CPU oversubscription is more important than maximizing raw throughput.

<a id="-stopping-a-run-ctrlc"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/circle-stop.svg" alt="" width="20" height="20"> Stopping a run (Ctrl+C)

Press **Ctrl+C** once in the terminal to interrupt a match, SPRT, or SPSA run. Tunex cancels games that have not started, signals active workers to stop, and closes their IronPhoenix subprocesses and redirected streams.

- **SPSA:** Each completed iteration is saved to `spsa.state_file`. Interruptions midway through an iteration do not commit a partial parameter update; rerun with the same configuration to resume from the last completed iteration.
- **Match/SPRT:** Finished game records in the JSONL results file remain on disk. Starting a new match or SPRT command does not resume the previous aggregate statistics.
- **Exit code:** Ctrl+C exits with code `130` rather than showing a Python traceback.

If IronPhoenix ignores `stop` / `quit`, Tunex force-terminates the subprocess rather than waiting indefinitely.

<a id="-result-termination"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/flag.svg" alt="" width="20" height="20"> Result termination

Tunex currently ends a game when:

- IronPhoenix returns `mate 1` for the chosen root move (immediate terminal king capture),
- IronPhoenix returns `bestmove 0000`; Tunex checks whether the side is in check and records checkmate or stalemate,
- or `max_plies` is reached, which is recorded as a draw.

If IronPhoenix exits, hangs, or violates the expected protocol, the experiment fails rather than silently counting a bogus result.

<a id="-suggested-ironphoenix-workflow"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/compass.svg" alt="" width="20" height="20"> Suggested IronPhoenix workflow

For isolated feature tests:

```text
baseline = current main/default parameter values
candidate = one changed parameter or feature
MultiPV = 1
same Hash
same time control
paired openings
same adjudication
```

For search tuning:

1. Use SPRT to establish that a search feature itself is healthy.
2. Expose its important constants as UCI spin parameters.
3. Use SPSA to tune several interacting constants.
4. Validate the final vector in a fresh long match/SPRT against the untouched baseline.

For HCE tuning, Tunex can already tune the exposed material and mobility UCI weights by games. A future offline/Texel mode can add much faster position-loss optimization without changing the match/SPSA layer.

<a id="-license"></a>

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/scale.svg" alt="" width="20" height="20"> License

GPL-3.0-or-later. See `LICENSE`.
