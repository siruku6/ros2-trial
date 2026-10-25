# Source ROS 2, the CRANE+ V2 workspace and this project's workspace (once built).
# setup.bash needs bash; skip quietly when /etc/profile is read by sh.
[ -n "${BASH_VERSION:-}" ] || return 0

source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"
source "${CRANE_WS:-/opt/crane_ws}/install/setup.bash"
if [ -f /ws/install/setup.bash ]; then
    source /ws/install/setup.bash
fi
