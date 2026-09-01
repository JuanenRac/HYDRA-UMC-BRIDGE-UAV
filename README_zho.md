<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - 搭载摄像头的无人机双向协调桥接
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-BRIDGE-UAV 横幅" width="100%">
</p>

# 🛩️ HYDRA-UMC-BRIDGE-UAV

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | 🇨🇳 <b>简体中文</b> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 🔗 HYDRA-UMC 与搭载摄像头的无人机(UAV)之间无依赖的协调边界

<p align="left">
  <img src="https://img.shields.io/badge/许可证-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="故障安全">
</p>

---

## 1. 🛠️ 技术概览

**HYDRA-UMC-BRIDGE-UAV** 是 HYDRA-UMC 与搭载摄像头的无人机(UAV)之间双向的高层协调边界,可通过 Wi-Fi、无线电链路或蜂窝(4G/5G)遥测连接访问。它校验并转发一套小型的、具名的高层飞行请求词汇(`PRE_FLIGHT_CHECK`、`TAKEOFF`、`GOTO_WAYPOINT`、`HOVER_AND_CAPTURE`、`RETURN_TO_LAUNCH`),并单独运行一个真实的、强制性的链路丢失心跳(heartbeat)看门狗。它从不计算飞行控制或姿态稳定,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停(E-STOP)。

它与 `HYDRA-UMC-BRIDGE-DROIDS` 和 `HYDRA-UMC-BRIDGE-AMR` 同属 **Mobile & Autonomous Bridges** 家族,并与固定式的 **External Automation Bridges**(CNC、LASER、OPENPNP、PRINTER3D、ROS2)共享同一个 `HYDRA-UMC-SDK` 任务与安全契约。

### 核心特性:
* ✅ **真实的无依赖飞行请求核心:** `coordinator.py` 中的 `UavCoordinator` 完全没有导入 MAVLink 或任何厂商 SDK——它刻意保持为纯 Python,可以在任何主机上测试,无需连接真实的 UAV。*(已实现,并在 `tests/test_coordinator.py` 中测试)*
* ✅ **真实的具名飞行请求词汇:** `PRE_FLIGHT_CHECK`、`TAKEOFF`、`GOTO_WAYPOINT`、`HOVER_AND_CAPTURE`、`RETURN_TO_LAUNCH`——绝不是原始的姿态/油门指令。正常任务完成的 `COMPLETE` 和紧急情况下的 `ABORT` 都会解析为同一个真实的 `RETURN_TO_LAUNCH` 请求。*(已实现)*
* ✅ **一个真实的、强制性的链路丢失心跳看门狗:** `HeartbeatMonitor` 是一个确定性的、由显式 `now` 驱动的故障保护状态机——它从不读取真实时钟,如果从未收到过观测就会从第一次检查开始就报告 `LOST`,并把恰好等于超时时刻的情况仍视为 `OK`(只有真正超出超时才会触发配置好的 `RETURN_TO_LAUNCH`/悬停故障保护)。*(已实现,并在 `tests/test_heartbeat.py` 中通过一整套确定性边界测试进行了测试)*
* ✅ **真实的共享安全门控:** 每个通过 `UavCoordinator.dispatch()` 派发的任务都会由 `HYDRA-UMC-SDK` 的 `bridge_contract` 中的 `evaluate_job()` 评估,这与所有兄弟桥接以及 HYDRA-UMC-SERVER 使用的是同一个门控;生产性阶段需要外部机器处于 `IDLE` 且 HYDRA-UMC 单元处于 `READY`,而 `ABORT` 在故障期间仍可请求。*(已实现)*
* ✅ **安全拒绝的阶段路由与静态证据:** 未知的未来 SDK 阶段会被拒绝。`inspect_request_plan.py` 会输出静态模式 `1.0` 的飞行请求计划,且不会打开任何传输通道。*(已实现,已测试)*
* ✅ **非变更式构建/测试:** `build-test.bat`/`.sh` 编译源码并运行确定性单元测试,不改变版本或 CHANGELOG。*(已实现,见下方"构建与运行")*
* 🔜 **真实的 MAVLink(Pixhawk/PX4)或 DJI OSDK 传输适配器**——只有在选定并测试了真实的飞控/SDK 之后才会引入。*(计划中)*

---

## 2. 🔄 UAV 协调流程

```mermaid
flowchart LR
    UAV["搭载摄像头的 UAV<br/>(Wi-Fi / 无线电 / 4G-5G 遥测)"] -- "飞行请求" --> BRIDGE["BRIDGE-UAV<br/>UavCoordinator.dispatch()"]
    UAV -- "心跳" --> HB["HeartbeatMonitor<br/>.observe() / .state()"]
    HB -- "LOST -> failsafe_action" --> BRIDGE
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> SERVER["HYDRA-UMC-SERVER"]
    SERVER -- "任务 / 中止" --> MCU["MCU 安全"]
```

---

## 3. 🧱 架构与设计决策

* **为什么心跳看门狗是独立的模块,而不是被并入协调器。** 链路丢失是一种真实的、与"这个任务现在是否被允许"完全不同的故障模式——`HeartbeatMonitor` 独立于任何具体任务,回答的是"我是否还能信任上一次从这台 UAV 收到的信息",因此它可以被持续检查(例如在每个遥测周期检查一次),而不仅仅是在恰好派发任务时才检查。
* **为什么 `HeartbeatMonitor` 接收显式的 `now`,而不是读取真实时钟。** 本项目起步时所依据的架构笔记明确指出,一个 UAV 桥接必须(REQUIRES)具备这个看门狗——要证明它在超时边界上的精确行为(而不只是"它最终会超时"),唯一的办法就是不依赖一个基于真实 sleep、缓慢且不稳定的测试套件,而是把时间变成一个显式的、可测试的输入。
* **为什么 `COMPLETE` 和 `ABORT` 都会解析为 `RETURN_TO_LAUNCH`。** 对一台 UAV 而言,完成任务和紧急中止有着相同的真实正确结果:回家。在静态计划中把它们收敛为同一个请求名称(去重,而不是重复列出),诚实地反映了这一点,而不是发明两个不同的"回家"动词。
* **为什么本桥接自身的心跳明确地不能替代飞控自身的故障保护。** Pixhawk/PX4 和 DJI 自身的固件已经在无线电/遥测层面实现了真实的、近乎经过认证的链路丢失故障保护——本桥接的 `HeartbeatMonitor` 是面向 HYDRA-UMC 自身状态的协调层信号,两者必须独立并存;参见 `docs/BRIDGE_GUIDE.md` 自身的硬件验收门控。
* **为什么 MAVLink/OSDK 传输适配器尚未加入本仓库。** 在选定并测试某个具体飞控真实的命令/遥测协议之前就对其做出承诺,会有引入这个本地无依赖核心无法验证的假设的风险。
* **它如何融入整个生态系统。** BRIDGE-UAV 位于真实的 UAV 与 `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → MCU 安全之间——它是一个协调边界,绝不是飞行控制节点,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停。

---

## 📂 目录结构

```text
HYDRA-UMC-BRIDGE-UAV/
├── src/
│   └── hydra_umc_bridge_uav/
│       ├── __init__.py
│       ├── coordinator.py       # UavCoordinator:无依赖的飞行请求门控
│       └── heartbeat.py         # HeartbeatMonitor:真实的、确定性的链路丢失故障保护
├── tests/
│   ├── test_coordinator.py      # 协调核心的确定性单元测试
│   └── test_heartbeat.py        # 心跳看门狗的确定性边界测试
├── tools/
│   ├── build_test.py            # 非变更式编译 + 测试运行器 (build-test.bat/.sh)
│   ├── bump_version.py          # 同步 pyproject.toml、清单和 CHANGELOG.md
│   └── inspect_request_plan.py  # 打印静态飞行请求计划(不打开传输通道)
├── docs/
│   └── BRIDGE_GUIDE.md          # 范围、兼容平台、脚本、硬件验收门控
├── build-test.bat / build-test.sh  # 仅验证,绝不修改仓库
├── build.bat / build.sh            # 先验证,成功后才更新版本 + CHANGELOG
├── pyproject.toml               # 包元数据;依赖 HYDRA-UMC-SDK (git)
├── hydra-umc.project.json       # 生态系统清单(版本、成熟度、家族)
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md / CONTRIBUTING.md / SECURITY.md / SUPPORT.md
├── LICENSE / LICENSE.md
└── README.md / README_*.md      # 本文件及其 6 种译文
```

---

## 4. ⚙️ 构建与运行

需要 Python 3.11+。`tools/build_test.py` 期望 `HYDRA-UMC-SDK` 作为兄弟目录被检出(`../HYDRA-UMC-SDK`),或通过环境变量 `HYDRA_UMC_SDK_ROOT` 指定。

```bash
# Windows
build-test.bat      # 仅验证 —— 不改变版本/CHANGELOG
build.bat            # 先验证,成功后更新版本 + CHANGELOG

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` 使用 `py_compile` 编译 `src/` 下的每个模块,并运行完整的 `unittest` 套件(`tests/test_coordinator.py`、`tests/test_heartbeat.py`)——以确定性的方式进行,没有真实 UAV 连接,没有网络,也不会改变版本/CHANGELOG。`build` 会先运行同样的验证,只有成功后才调用 `tools/bump_version.py`,在 `pyproject.toml`、`hydra-umc.project.json` 和 `CHANGELOG.md` 之间同步版本号。目前尚无真正的硬件 `run` 命令——这需要经过验证的 MAVLink/OSDK 传输适配器和真实的 UAV。

---

## ✅ 当前状态与后续步骤

**目前真实的部分:** 版本 `0.0.1`,作为一个无依赖协调核心(`UavCoordinator`)是功能齐备的,并配有一个真实的、经过完整边界测试的链路丢失心跳看门狗(`HeartbeatMonitor`)、安全拒绝的阶段路由、静态 `plan-only` 飞行请求模式,以及已接入 CI 并带 SDK 检出的非变更式 build-test 脚本。

**集成边界:** 本桥接只是一个协调边界——它不是飞行控制节点,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停;每个被派发的任务仍然要经过所有兄弟桥接使用的同一个共享门控。`HeartbeatMonitor` 自身的故障保护信号是协调层的事务,绝不能替代飞控自身独立的链路丢失故障保护。

**仍待完成:** 尚未验证任何真实的 MAVLink(Pixhawk/PX4)或 DJI OSDK 传输方式,也没有物理 UAV——真实的适配器只会在选定并测试了具体的飞控/SDK 之后才会引入。

---

## 🔗 相关项目

本项目是同一作者(JuanenRac / Electro Hobby 3D)更大的机器人生态系统的一部分,涵盖固件、控制软件、AI 节点和车队工具。了解这一点很有必要,因为某个请求实际上可能与这些项目之一有关,而不是与本仓库有关。

### 直接相关

- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** —— 共享的任务与安全契约,本桥接(以及所有其他桥接)都通过它评估任务。
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** —— 本桥接汇报的经过身份验证的生态系统边界。
- **[HYDRA-UMC-BRIDGE-DROIDS](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-DROIDS)** —— 面向有腿式/人形机器人的兄弟移动桥接。
- **[HYDRA-UMC-BRIDGE-AMR](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-AMR)** —— 面向 AGV/AMR 车队的兄弟移动桥接。

### 生态系统的其余部分

**HYDRA-UMC 平台** —— 本桥接为其协调辅助功能的多机器人微工厂
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** —— 协调多达 8 条机械臂的 CM5 + STM32H745 主板。
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** —— 每个控制客户端和桥接都会对接的 Express/WebSocket 后端。
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** —— 基于网页的控制仪表盘,多机器人 3D 可视化。

**External Automation Bridges** —— 共享同一个 `HYDRA-UMC-SDK` 任务门控的兄弟仓库
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** —— CNC 单元协调桥接。
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** —— 激光单元协调桥接。
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** —— 面向 OpenPnP 的板级流程桥接。
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** —— 面向开源 3D 打印软件的协调桥接。
- **[HYDRA-UMC-BRIDGE-ROS2](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-ROS2)** —— 面向任意 ROS 2 平台的通用协调桥接。

**安全与集成证据**
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** —— 整个桥接家族共用的单元区域安全证据。
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** —— 硬件在环测试证据。

## 👤 作者
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com
📺 [youtube.com/@electrohobby3d](https://youtube.com/@electrohobby3d)

## 📜 许可证
GPL-3.0 - 详见 LICENSE。
