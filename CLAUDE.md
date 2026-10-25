# ros2-trial

Final assignment for the Physical AI course: CRANE+ V2 in Gazebo picks the object named in a
text instruction and places it in the named box, on ROS 2 Jazzy.

## Read first

- `docs/plans/261025_final_assignment_plan.md` (Japanese) — task design, schedule and split of
  work (§4), decisions already made by the user (§5). Do not reopen §5 decisions.
- `README.md` — how to build and enter the Docker environment.

## Environment

- Everything runs in Docker. Run commands as
  `docker compose exec -u ubuntu ros bash -lc '<command>'`.
  On the GPU machine, start the container with `-f compose.yaml -f compose.gpu.yaml`.
- The repo is mounted at `/ws`. GUI apps open on the noVNC desktop (`DISPLAY=:1`).
  Build with `cd /ws && colcon build --symlink-install`; the packages are in `src/`.
- Stop ROS processes with SIGINT. After a SIGKILL, run `fastdds shm clean` in the container,
  or nodes started later may fail to talk to each other. Launch logs can contain non-UTF-8
  bytes; use `grep -a` on them.
- The GPU machine has little free disk (71 GB after the first image build on 2026-10-25) and
  is shared with other projects' images and containers; never touch those. Keep one image; run
  `docker builder prune` after rebuilds; check `docker system df`.
- Python libraries: install the latest release at install time (organization rule).
  ROS 2 stays on Jazzy, and ROS nodes stay on Jazzy's apt NumPy 1.26; the reasons are in
  README §3. Never pip-install into the system Python anything that pulls in NumPy 2;
  put such libraries in a venv (plan §6).

## Rules from the assignment

- No real name, affiliation or email in any file, commit, video or report. Commits must be
  authored as `siruku6 <12032476+siruku6@users.noreply.github.com>`; check `git config user.name`
  and `git config user.email` before the first commit in a new clone.
- Code reused from rt-net/crane_plus must be credited in README and the report, and kept
  separate from code written here.

## Primary sources

- Assignment requirements: the course PDF「最終課題（10_19 改訂版）」(not in the repo; ask the user).
- CRANE+ V2: https://github.com/rt-net/crane_plus, jazzy branch at commit `34c4584`
  (pinned in `docker/Dockerfile`).
- Base image: https://github.com/Tiryoh/docker-ros2-desktop-vnc
