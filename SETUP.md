# Setup

1) Put these files into your **profile repo** (the repo named exactly like your username): `ishyv/ishyv`.

2) Commit + push.

3) Go to the repo tab: **Actions**
   - Enable Actions if GitHub asks.
   - Run the workflows manually once (**workflow_dispatch**) so the generated SVGs show up.

Notes:
- This design avoids external stat widgets and avoids most Camo drama by generating assets inside your repo.
- The placeholders (`assets/metrics.svg`, `assets/snake.svg`, `profile-3d-contrib/profile-night-rainbow.svg`) will be overwritten by workflows.
