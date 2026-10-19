# Push the prepared source yourself

This project is a local Git repository with an MIT license. No GitHub repository has been created, no remote is configured, and nothing has been pushed by this preparation.

Create an empty public repository in your GitHub account, then run:

```sh
cd ~/Desktop/projects/resume-after-reset-mvp
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the account and repository placeholders with your real URL. Authenticate using your own normal GitHub setup. Avoid initializing the remote with another README or license if you want a straightforward first push.

Runtime databases, local logs, Python caches, environment files, macOS metadata and `.private/` development history are excluded by `.gitignore`. Synthetic identifiers are used in public tests. Review the staged files and commit history before publishing.

The GitHub Actions workflow runs Python tests, UI checks and the local package-reference validator. It does not install the macOS service, contact a real account, send messages, or demonstrate OpenAI approval.

Official directory review is a separate process; see `SUBMISSION.md` for the current local-MCP eligibility restriction and remaining materials.
