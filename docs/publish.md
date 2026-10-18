# Repository publication

Repository: [Afifah48/uav-telemetry-link-monitor](https://github.com/Afifah48/uav-telemetry-link-monitor).

The `main` branch starts with an empty review-base commit. All source, documentation, tests, screenshots and measured results are committed on `feature/telemetry-monitor`. Merge the pull request only after review and approval.

Manual equivalents, using the official [GitHub CLI](https://cli.github.com/), from the project root:

```bash
gh auth login
# Only if the repository does not already exist:
gh repo create Afifah48/uav-telemetry-link-monitor --public --description "Read-only MAVLink telemetry monitoring with measured UDP impairment experiments"
# Only if origin is absent:
git -c safe.directory="$PWD" remote add origin https://github.com/Afifah48/uav-telemetry-link-monitor.git
# Push only the empty bootstrap main when setting up a new empty repository:
git -c safe.directory="$PWD" push -u origin main
git -c safe.directory="$PWD" push -u origin feature/telemetry-monitor
gh pr create --repo Afifah48/uav-telemetry-link-monitor --base main --head feature/telemetry-monitor --title "Implement read-only MAVLink telemetry monitor and reliability experiments" --body-file docs/pull-request.md
```

The command-scoped ownership exception trusts only the current root for that invocation; it accommodates sandbox-created Git metadata without changing global Git settings. Existing repositories/history must be inspected rather than overwritten. Never force-push over unrelated work.
