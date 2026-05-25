# Repository Agent Notes

- This is a Python/Jinja2 static creator dashboard for one creator IP across Bilibili, Douyin, and Xiaohongshu.
- Keep scope to the single-creator dashboard. Do not add MCN, multi-account, or permission-system features unless explicitly requested.
- Never commit real cookies, tokens, passwords, Bark keys, raw sensitive responses, local env files, caches, or virtual environments.
- Missing platform fields should render as `null`, `--`, or clear status text. Do not estimate unavailable values as if they were real data.
- GitHub Pages only deploys static output. Scheduled platform fetching is expected to run from the NAS, not GitHub Actions.
- For NAS cloud publishing, prefer host Git + SSH deploy key + `scripts/nas_update_and_push_cloud.sh`.
