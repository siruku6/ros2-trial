# ros2-trial

Physical AI 応用1講座の最終課題です（作成中）。
文で指示した物体を、小型アーム CRANE+ V2 が Gazebo 上で探して掴み、指定の箱に仕分けることを目指しています。
計画は [docs/plans/261025_final_assignment_plan.md](docs/plans/261025_final_assignment_plan.md) にあります。

## 目次

- [1. 環境構築](#1-環境構築)
  - [1.1 必要なもの](#11-必要なもの)
  - [1.2 コンテナを起動する](#12-コンテナを起動する)
  - [1.3 画面を見る](#13-画面を見る)
  - [1.4 コンテナの中でコマンドを実行する](#14-コンテナの中でコマンドを実行する)
- [2. 動かし方](#2-動かし方)
- [3. 使っているもの](#3-使っているもの)

## 1. 環境構築

ROS 2・Gazebo・ロボットのパッケージをすべて Docker の中に入れています。
ホストには Docker だけを入れれば動きます。

### 1.1 必要なもの

| 用途 | 必要なもの |
|---|---|
| すべての場合 | Docker と Docker Compose |
| GPU を使う場合 | NVIDIA ドライバと nvidia-container-toolkit |

### 1.2 コンテナを起動する

リポジトリの直下で次を実行します。
初回はイメージの作成に時間がかかります（2026-10-25 に GPU マシンで約 15 分。ディスクを約 13 GB 使った）。

```bash
docker compose build

# GPU なし
docker compose up -d

# GPU あり
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

リポジトリはコンテナの `/ws` に見えます。
ホストで編集したファイルは、そのままコンテナの中に反映されます。

### 1.3 画面を見る

Gazebo などの画面は、ブラウザから noVNC（ブラウザで画面を操作する仕組み）で見ます。
パスワードは `ubuntu` です。

| Docker を動かしているマシン | 開き方 |
|---|---|
| 手元のマシン | ブラウザで `http://localhost:6080` を開く |
| 遠隔のマシン | 手元で `ssh -L 6080:localhost:6080 <遠隔のマシン>` を実行してから、ブラウザで `http://localhost:6080` を開く |

ポートは外部に公開せず、Docker を動かしているマシンの中からだけつなげるようにしています。

### 1.4 コンテナの中でコマンドを実行する

`bash -l` で入ると、ROS 2 と CRANE+ V2 のパッケージが使える状態になります。

```bash
docker compose exec -u ubuntu ros bash -l
```

ここから起動した Gazebo などの画面は、1.3 の noVNC の画面に出ます。

## 2. 動かし方

今は、CRANE+ V2 の公式パッケージにある例を動かせる状態です。
カメラで机の上の赤いキューブを見つけ、腕で掴んで、腕の右側に置きます。

1.4 の方法で端末を 2 つ開き、それぞれで次を実行します。
端末 2 は、端末 1 で Gazebo と RViz の画面が出て、腕の制御の準備が終わってから（起動から 20 秒ほど）実行します。

```bash
# 端末 1: 赤いキューブを置いたワールドを、カメラ付きで起動する
ros2 launch crane_plus_gazebo crane_plus_with_red_cube.launch.py use_camera:=true

# 端末 2: 赤い物体を探すノードと、掴むノードを起動する
ros2 launch crane_plus_examples_py camera_example.launch.py example:='color_detection' use_sim_time:=true
```

端末 2 を実行すると、腕は待機の姿勢に移り、キューブが 3 秒止まって見えた時点で掴みに行きます。
置き終わるまで 1 分ほどです。
RViz の右側には、カメラ画像と、赤い領域を白く塗った画像が出ます。

置いたキューブはカメラの視野から外れるので、腕は待機の姿勢のまま止まります。
もう一度見るには、両方の端末を Ctrl+C で止めてから起動し直します。

## 3. 使っているもの

| もの | 出典 |
|---|---|
| ROS 2 Jazzy と noVNC の Docker イメージ | [Tiryoh/docker-ros2-desktop-vnc](https://github.com/Tiryoh/docker-ros2-desktop-vnc) |
| CRANE+ V2 の ROS 2 パッケージ | [rt-net/crane_plus](https://github.com/rt-net/crane_plus)（jazzy ブランチ） |
| 参考にした講座のリポジトリ | [matsuolab/physicalai_pubsub](https://github.com/matsuolab/physicalai_pubsub) |

ROS 2 は、最新の長期サポート版の Lyrical ではなく Jazzy を使っています。
CRANE+ V2 の ROS 2 パッケージが Jazzy までしか対応しておらず、その中で最新の長期サポート版が Jazzy だからです。

ROS 2 のノードが使う NumPy も、最新の 2 系ではなく、Jazzy の apt パッケージの 1.26 です。
Jazzy の OpenCV や cv_bridge（ROS 2 の画像メッセージと OpenCV の画像を相互に変換する部品）が NumPy 1 系向けにビルドされており、NumPy 2 系では読み込みの時点で失敗するからです。
