# <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/git-pull-request.svg" alt="" width="20" height="20"> Contributing to TuneX

Thank you for helping improve **TuneX**, the Python harness for UCI engine testing, SPRT, SPSA, and Bayesian/TPE tuning. Contributions to IronPhoenix four-player Teams chess and SpellWarden Spell Chess support are welcome.

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/book-open.svg" alt="" width="20" height="20"> Before you start

- Search existing issues and pull requests before opening a duplicate.
- For large changes or new algorithms, open an issue first to discuss behavior, configuration, reproducibility, and testing.
- Keep changes focused. Explain why they help and what compatibility they affect.
- Avoid reporting chess-engine strength improvements without supporting match data. Noisy short tests are not proof of Elo gains.
- Do not include proprietary engine binaries, personal data, private keys, or copyrighted datasets without permission.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/terminal.svg" alt="" width="20" height="20"> Development setup

TuneX requires **Python 3.11+**.

```bash
git clone https://github.com/KnightStackSys/Tunex.git
cd Tunex
python -m pip install -e ".[dev]"
python -m compileall -q src
python -m pytest -q
```

The CI matrix runs on **Windows and Ubuntu**, with Python **3.11 and 3.13**. Run the same checks locally before requesting review.

For a quick command-line check:

```bash
tunex --help
tunex -c configs/ironphoenix.example.json probe
```

The `probe` command needs a compatible, built engine executable at the path specified in the config. It is not required for the regular mock-based Python test suite.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/folder-tree.svg" alt="" width="20" height="20"> Project layout

| Path | Purpose |
| --- | --- |
| `src/tunex/engine.py` | UCI process lifecycle, options, and search responses |
| `src/tunex/match.py` | Variant-specific matches, openings, adjudication, and paired games |
| `src/tunex/config.py` | Configuration models and validation |
| `src/tunex/sprt.py` | Sequential hypothesis testing |
| `src/tunex/spsa.py` | Simultaneous perturbation tuning |
| `src/tunex/bayesian.py` | Resumable Bayesian/TPE parameter optimization |
| `src/tunex/cli.py` | CLI commands and output |
| `configs/` | Example engine and tuning configurations |
| `tests/` | Automated regression tests |

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/shield-check.svg" alt="" width="20" height="20"> Implementation guidelines

**Preserve variant correctness.** IronPhoenix uses Red + Yellow versus Blue + Green in a four-player turn cycle. SpellWarden uses White versus Black with complete spell turns such as `freeze@d5:e2e4`. Do not assume they share move notation, starting positions, terminal conditions, or check rules.

**Keep experiments reproducible.** Use seeded randomness, paired openings when applicable, explicit engine and time-control settings, and resumable experiment state. Reject incompatible state files instead of silently mixing experiments.

**Respect UCI.** Validate option names, wait for `readyok`, send `ucinewgame` at the start of each game, handle timeouts and interruption, and close all engine processes even on failures.

**Keep optimization honest.** SPSA and Bayesian optimization choose candidates; SPRT evaluates a hypothesis. Separate tuning matches from independent validation matches. Report W/D/L, game counts, time controls, opening sources, and confidence or uncertainty when claiming a strength change.

**Use standard Python conventions.** Include type hints when helpful, meaningful names, small functions, clear error messages, and tests for failure cases. The project currently targets the standard library for runtime dependencies; discuss new mandatory dependencies before adding them.

**Use Lucide icons in Markdown.** Follow the existing README convention: accessible icons from `https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/{icon-name}.svg`, with lowercase kebab-case filenames. Icons should aid navigation, not replace text.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/flask-conical.svg" alt="" width="20" height="20"> Tests and benchmarks

For every behavior change, add or update tests in `tests/`. Especially cover:

- Option parsing and malformed UCI responses.
- Paired seat/color assignment and correct result scoring.
- Four-player terminal king captures and Spell Chess king/check adjudication.
- Opening formats, promotions, spell turns, and invalid moves.
- Timeouts, cancellation, worker shutdown, and no leaked processes.
- Deterministic RNG behavior, state persistence, and safe resumption.
- Regression behavior for the other engine variant.

Use mock engines for fast unit tests. For performance- or Elo-related changes, provide a reproducible real-engine benchmark when practical. Do not make the main test suite depend on local engine binaries or network access.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons/git-branch.svg" alt="" width="20" height="20"> Pull request workflow

1. Fork the repository or create a focused feature branch from `main`.
2. Implement one coherent change and include regression tests and documentation.
3. Run `python -m compileall -q src` and `python -m pytest -q`.
4. Open a pull request explaining the motivation, behavior, tests, and any compatibility risks.
5. Address review feedback and check CI before merging.

A useful pull request description includes **Summary**, **Why**, **Testing**, and, for strength-related changes, **Match methodology and results**. Please link related issues when applicable.

Project maintainers decide whether and when to merge changes. A passing test suite does not guarantee acceptance.

## <img src="https://cdn.jsdelivr.net/npm/lucide-static@latest/icons-scale.svg" alt="" width="20" height="20"> Licensing

TuneX is licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. By submitting a contribution, you agree to make it available under that project license and confirm that you have the right to contribute the material. Consult [LICENSE](LICENSE) for the full terms.
