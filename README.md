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
  - [2.1 ビルドする](#21-ビルドする)
  - [2.2 文の指示で物体を仕分ける](#22-文の指示で物体を仕分ける)
  - [2.3 crane_plus の既存例を動かす](#23-crane_plus-の既存例を動かす)
- [3. 使っているもの](#3-使っているもの)
  - [3.1 外部のソフトウェアと参考にしたもの](#31-外部のソフトウェアと参考にしたもの)
  - [3.2 crane_plus から流用したコード](#32-crane_plus-から流用したコード)
  - [3.3 ROS 2 と NumPy の版](#33-ros-2-と-numpy-の版)
- [4. ファイルの構成](#4-ファイルの構成)

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

今は、色で物体を探す方式で、文の指示どおりに物体を箱に仕分けられます（計画書の案 B）。

### 2.1 ビルドする

このリポジトリのパッケージは `src/` にあります。
最初に一度、1.4 の方法でコンテナに入って、次を実行します。

```bash
cd /ws
colcon build --symlink-install
source install/setup.bash
```

`--symlink-install` を付けると、Python のコードと設定ファイルの変更は、ビルドし直さなくても次の起動から反映されます。
action の定義（`src/crane_sorter_interfaces/`）や `setup.py` を変えたときは、ビルドし直します。
次からは、1.4 の方法で入るだけでビルドの結果が使える状態になります。

### 2.2 文の指示で物体を仕分ける

机の上の赤・緑・青のキューブを、文の指示どおりに左右の箱に入れます。
物体の位置は起動のたびに変わります。
仕組みは [docs/design/261025_sorting_design.md](docs/design/261025_sorting_design.md) で説明しています。

1.4 の方法で端末を 3 つ開き、上から順に実行します。

```bash
# 端末 1: 机・腕・カメラ・箱・物体を Gazebo に用意する
# seed:=1 のように乱数の種を指定すると、同じ配置を再現できる
ros2 launch crane_sorter sim.launch.py

# 端末 2: 物体を探すノードと、掴んで箱に置く action のサーバを起動する
# 端末 1 で腕の制御の準備が終わってから（起動から 20 秒ほど）実行する
ros2 launch crane_sorter sorter.launch.py

# 端末 3: 指示を送る。複数並べると順に実行する
# 端末 2 に「依頼を待っています」と出てから実行する
ros2 run crane_sorter send_instruction "赤いブロックを右の箱へ" "青いブロックを左の箱へ"
```

端末 3 には、依頼ごとに段階と結果が出ます。1 件 30 秒ほどです。

```text
[1/2] 「赤いブロックを右の箱へ」→ 物体 red を箱 right へ
[1/2]   段階: looking
[1/2]   段階: approaching
...
[1/2] 成功: 物体 red を箱 right に置きました
```

指示に使える言葉は、物体が「赤」「緑」「青」（red、green、blue）、箱が「右」「左」（right、left）です。
左右は腕から見た向きです。Gazebo の初期の視点は腕と向かい合っているので、画面では右の箱が左側に見えます。
指示の実行中に端末 3 で Ctrl+C を押すと、取り消しを求めます。取り消せるのは物体を掴む前だけです。

物体を探した結果は、次のコマンドで画像として見られます。

```bash
ros2 run rqt_image_view rqt_image_view /color_detector/detections
```

ノードを Ctrl+C 以外の方法（`kill -9` など）で止めると、そのあと起動したノードどうしの通信が通らなくなることがあります。
そのときは、すべての端末を Ctrl+C で止め、`fastdds shm clean` を実行してから起動し直します。

### 2.3 crane_plus の既存例を動かす

CRANE+ V2 の公式パッケージにある例です。
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

### 3.1 外部のソフトウェアと参考にしたもの

| もの | 出典 |
|---|---|
| ROS 2 Jazzy と noVNC の Docker イメージ | [Tiryoh/docker-ros2-desktop-vnc](https://github.com/Tiryoh/docker-ros2-desktop-vnc) |
| CRANE+ V2 の ROS 2 パッケージ | [rt-net/crane_plus](https://github.com/rt-net/crane_plus)（jazzy ブランチ、コミット `34c4584`） |
| 参考にした講座のリポジトリ | [matsuolab/physicalai_pubsub](https://github.com/matsuolab/physicalai_pubsub) |

### 3.2 crane_plus から流用したコード

次のファイルは、rt-net/crane_plus（jazzy ブランチ、コミット `34c4584`）のファイルを元に書き換えたものです。
元のファイルは Apache License 2.0 で配布されているので、書き換えたファイルも Apache License 2.0 のままにし、各ファイルの先頭に元のファイルと変えた点を書いています。
ライセンスの本文は [src/crane_sorter/LICENSE.crane_plus](src/crane_sorter/LICENSE.crane_plus) にあります。

| このリポジトリのファイル（`src/crane_sorter/` 以下） | 元にしたファイル | 主に変えた点 |
|---|---|---|
| `crane_sorter/arm.py` | `crane_plus_examples_py/crane_plus_examples_py/pick_and_place_tf.py` | 腕とグリッパを動かす部分だけを取り出してクラスにした。掴む・置くの手順は自作の `pick_and_place_server.py` に移した |
| `crane_sorter/color_detector.py` | `crane_plus_examples_py/crane_plus_examples_py/color_detection.py` | 複数の色に対応した。位置をカメラの高さの決め打ちではなく tf から計算する。画像の端のかたまりを捨てる |
| `launch/sorter.launch.py` | `crane_plus_examples_py/launch/camera_example.launch.py` | 起動するノードを自作のものに替えた |
| `worlds/sorting_table.sdf` | `crane_plus_gazebo/worlds/table.sdf` | ArUco マーカー付きのキューブを除いた |
| `crane_sorter/world_models.py` のキューブの物理特性 | `crane_plus_gazebo/models/aruco_cube_0/model.sdf` | 衝突形状・質量・摩擦の値を写し、見た目を単色にした |

次のものは、コピーせずに crane_plus のパッケージから直接使っています。

- `crane_plus_gazebo` の `crane_plus_with_table.launch.py`（Gazebo・腕・カメラ・MoveIt の起動）
- `crane_plus_examples_py` の `plan_and_execute` 関数と、MoveItPy の設定ファイル

それ以外のファイルは、このリポジトリで書いたものです（MIT License）。

### 3.3 ROS 2 と NumPy の版

ROS 2 は、最新の長期サポート版の Lyrical ではなく Jazzy を使っています。
CRANE+ V2 の ROS 2 パッケージが Jazzy までしか対応しておらず、その中で最新の長期サポート版が Jazzy だからです。

ROS 2 のノードが使う NumPy も、最新の 2 系ではなく、Jazzy の apt パッケージの 1.26 です。
Jazzy の OpenCV や cv_bridge（ROS 2 の画像メッセージと OpenCV の画像を相互に変換する部品）が NumPy 1 系向けにビルドされており、NumPy 2 系では読み込みの時点で失敗するからです。

## 4. ファイルの構成

| 場所 | 中身 |
|---|---|
| `docker/`、`compose.yaml`、`compose.gpu.yaml` | Docker の環境（1 章） |
| `src/crane_sorter_interfaces/` | 「1 個掴んで箱に置く」action の定義 |
| `src/crane_sorter/crane_sorter/` | ノードの Python のコード（物体を探す、掴んで置く、指示を送る）と、配置を決める処理 |
| `src/crane_sorter/launch/` | Gazebo の環境を用意する `sim.launch.py` と、ノードを起動する `sorter.launch.py` |
| `src/crane_sorter/config/` | 箱と物体の配置（`layout.yaml`）と、色の範囲（`color_detector.yaml`） |
| `src/crane_sorter/worlds/` | 机だけの Gazebo のワールド |
| `src/crane_sorter/test/` | 指示の解釈と配置の決め方の単体テスト。`python3 -m pytest src/crane_sorter/test` で実行する |
| `docs/plans/` | 作業の計画 |
| `docs/design/` | 仕組みの説明 |
