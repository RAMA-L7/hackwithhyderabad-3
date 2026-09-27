# Collaboration Workflow (Rama + Mukul)

GitHub is the single source of truth. `main` is the stable shared branch.

## First-time setup

```powershell
git clone <repository-url>
Set-Location hackwithhyderabad-3
git checkout main
git pull origin main
```

(Replace `<repository-url>` with the private repo URL once created.)

## Creating a branch

```powershell
git checkout -b rama-planning
```

or

```powershell
git checkout -b mukul-ideas
```

## Saving work

```powershell
git status
git add .
git commit -m "Clear description of the change"
git push -u origin <branch-name>
```

## Updating before new work

```powershell
git checkout main
git pull origin main
```

Then switch back to your working branch. If `main` moved while you worked, merge it into your branch and resolve conflicts locally before opening a PR.

## Pull Request workflow

1. Push your branch to GitHub.
2. On GitHub, open a Pull Request from your branch into `main`.
3. The teammate reviews: checks content, neutrality (no premature winner), and no secrets.
4. Address review comments with follow-up commits on the same branch.
5. Merge the PR into `main`, then delete the branch if done.
6. Both teammates run `git checkout main` and `git pull origin main` to sync.
