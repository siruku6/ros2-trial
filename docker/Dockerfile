# ROS 2 Jazzy desktop served over noVNC, plus the CRANE+ V2 packages.
# The base image is pinned to a dated tag so that a rebuild gives the same environment.
FROM tiryoh/ros2-desktop-vnc:jazzy-20261018T0848

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV ROS_DISTRO=jazzy
ENV CRANE_WS=/opt/crane_ws

# crane_plus_examples_py, which this project reuses, is newer than the 3.0.0 binary
# release, so the jazzy branch is built from source at a fixed commit.
ARG CRANE_PLUS_COMMIT=34c4584c7373463f319174bb76a2198943ab9cb8

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3-rosdep \
    && git clone https://github.com/rt-net/crane_plus.git "${CRANE_WS}/src/crane_plus" \
    && git -C "${CRANE_WS}/src/crane_plus" checkout "${CRANE_PLUS_COMMIT}" \
    && (rosdep init || true) \
    && rosdep update --rosdistro "${ROS_DISTRO}" \
    && rosdep install -y --from-paths "${CRANE_WS}/src" --ignore-src --rosdistro "${ROS_DISTRO}" \
    && rm -rf /var/lib/apt/lists/*

RUN source "/opt/ros/${ROS_DISTRO}/setup.bash" \
    && cd "${CRANE_WS}" \
    && colcon build \
    && rm -rf build log

# The base image pip-installs NumPy 2 into /usr/local as a dependency of websockify (noVNC).
# It shadows the apt NumPy 1.26 that Jazzy's OpenCV, SciPy and cv_bridge are built against,
# and the crane_plus examples crash on import. websockify also runs on NumPy 1.26.
RUN python3 -m pip uninstall -y --break-system-packages numpy

# The AI object detector runs on the venv's Python, so that its libraries can use NumPy 2
# without breaking the ROS 2 nodes above. rclpy and the message packages come from /opt/ros
# through PYTHONPATH and work with NumPy 2 (checked on 2026-10-27; cv_bridge does not).
ENV AI_VENV=/opt/ai-venv
COPY docker/requirements-ai.txt /tmp/requirements-ai.txt
RUN python3 -m venv "${AI_VENV}" \
    && "${AI_VENV}/bin/pip" install --no-cache-dir -r /tmp/requirements-ai.txt \
    && rm /tmp/requirements-ai.txt

# Model weights are downloaded on first use into this directory, which compose.yaml mounts as a
# named volume so that they survive recreating the container.
ENV MODEL_CACHE=/opt/model-cache
ENV HF_HOME=${MODEL_CACHE}/huggingface
RUN mkdir -p "${MODEL_CACHE}" && chown 1000:1000 "${MODEL_CACHE}"

# Ultralytics reads its settings from YOLO_CONFIG_DIR. Its default weights directory is relative
# to the current directory, which put CLIP weights into the repo, and it sends usage data unless
# sync is off. Both are set here, before the first import at runtime.
ENV YOLO_CONFIG_DIR=/opt/ultralytics
RUN mkdir -p "${YOLO_CONFIG_DIR}" \
    && "${AI_VENV}/bin/python" -c "from ultralytics import settings; \
settings.update(weights_dir='${MODEL_CACHE}/ultralytics', sync=False)" \
    && chown -R 1000:1000 "${YOLO_CONFIG_DIR}"

# Login shells (docker compose exec ... bash -l) read /etc/profile.d;
# terminals opened on the noVNC desktop read /etc/bash.bashrc.
COPY docker/ros_env.sh /etc/profile.d/ros2_trial.sh
RUN echo 'source /etc/profile.d/ros2_trial.sh' >> /etc/bash.bashrc
