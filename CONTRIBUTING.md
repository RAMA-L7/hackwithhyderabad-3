# Contributing — HackwithHyderabad 3.0 Planning

## Branches

- `main` is the stable shared branch. Do not push substantial changes directly to it.
- Create a branch for your work with a descriptive name, e.g. `rama-planning`, `mukul-ideas`.

## Workflow

1. Update your local `main` before starting:
   `git checkout main` then `git pull origin main`
2. Create a branch: `git checkout -b <your-branch>`
3. Commit logical, reviewable changes with clear messages.
4. Push the branch: `git push -u origin <your-branch>`
5. Open a Pull Request into `main` and request review.
6. Merge only after review.

## Planning discipline

- Record project decisions in `docs/decision-log.md`.
- Keep comparisons neutral — no winner until the team decides together.
- Separate hypotheses and proposed metrics from measured results.

## Secrets

- Never commit API keys, tokens, passwords, private keys, or `.env` files.
- These are already ignored via `.gitignore`. If you spot a secret in history, report it before pushing.
