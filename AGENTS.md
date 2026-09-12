# MovieStar agent instructions

Read and follow [`CLAUDE.md`](CLAUDE.md). Its development rules apply to every
coding agent, regardless of product or model.

## Human-only merge boundary

Never merge a pull request or enable auto-merge. Agents may create and update
pull requests, push feature branches, address review feedback, and monitor CI.
Once a pull request is ready, stop and hand it to a human repository owner.

Never publish a package, create a release, change repository visibility, or add
credentials unless a repository owner explicitly authorizes that exact action.
