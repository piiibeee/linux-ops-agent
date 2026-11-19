# K8S 部署（k3s 单节点）

> 目标：在 WSL2 里跑一个真实的 Kubernetes 集群，把这个 Agent 部署进去，
> 并演示**自愈、滚动更新、扩缩容**三件运维岗必问的事。
> 全程约 1–2 小时，不需要买服务器，不需要云账号。
>
> 为什么用 k3s 而不是 minikube / kind：k3s 是单个二进制、自带 containerd 和
> StorageClass，最接近生产形态；kind 是拿 Docker 套娃，面试问"节点怎么起来"
> 时说不清。

---

## 0. 前置检查：WSL2 必须开了 systemd

k3s 以 systemd 服务方式运行，WSL2 默认没有 systemd。先确认：

```bash
ps -p 1 -o comm=
```

- 输出 `systemd` → 跳过本节
- 输出 `init` → 需要开启，继续往下

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```

然后在 **Windows 终端**里执行（会关掉所有 WSL 会话）：

```powershell
wsl --shutdown
```

重新打开 Ubuntu，再验一次 `ps -p 1 -o comm=`，现在应该是 `systemd`。

> 内存建议 ≥ 4G。默认 WSL 只给一半内存，如果 `free -h` 显示小于 4G，
> 在 Windows 用户目录建 `C:\Users\<你>\.wslconfig`：
> ```
> [wsl2]
> memory=6GB
> ```
> 然后 `wsl --shutdown` 生效。

---

## 1. 安装 k3s

```bash
curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -
```

国内用这个镜像地址，比官方 get.k3s.io 快很多。

验证：

```bash
sudo k3s kubectl get nodes
```

期望看到一行 `Ready`：

```
NAME              STATUS   ROLES                  AGE   VERSION
desktop-xxxx      Ready    control-plane,master   30s   v1.31.x+k3s1
```

配置免 sudo 的 kubectl：

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
source ~/.bashrc
kubectl get nodes
```

> k3s 里 `kubectl` 已经自带（软链到 k3s），不用另装。

---

## 2. 构建镜像并导入集群

k3s 用 containerd 而不是 Docker，`docker build` 出来的镜像**它看不到**，
必须导入。

```bash
cd ~/linux-ops-agent

# 构建（本机需要 Docker；没有就跳过，见下面"没装 Docker"）
docker build -t ops-agent-api:1.0 .

# 导出并导入 k3s 的 containerd
docker save ops-agent-api:1.0 -o /tmp/api.tar
sudo k3s ctr images import /tmp/api.tar

# 确认镜像在集群里了
sudo k3s ctr images ls | grep ops-agent
```

**没装 Docker 的话**，用 k3s 自带的 buildkit 也行，但更省事的办法是先装 Docker：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
```

---

## 3. 部署

```bash
cp k8s/01-secret.example.yaml k8s/01-secret.yaml
nano k8s/01-secret.yaml      # 把 DASHSCOPE_API_KEY 换成真实值，Ctrl+O 保存 Ctrl+X 退出
```

```bash
kubectl apply -f k8s/
```

按顺序起，第一个起来再起第二个（Redis 要给 api 当依赖）：

```bash
kubectl -n ops-agent get pods -w
```

期望最终：

```
NAME                     READY   STATUS    RESTARTS   AGE
api-xxxxxxxxxx-xxxxx     1/1     Running   0          40s
redis-xxxxxxxxxx-xxxxx   1/1     Running   0          70s
```

`Ctrl+C` 退出 watch。

---

## 4. 验收四件事（这就是面试要讲的）

### 4.1 服务能通

```bash
kubectl -n ops-agent port-forward svc/api 8000:8000
```

另开一个终端：

```bash
curl http://localhost:8000/api/health
```

返回 JSON 说明跑通了。浏览器也能开 `http://localhost:8000/docs` 看接口文档。

### 4.2 自愈

删掉 Pod，看它自己长回来：

```bash
kubectl -n ops-agent get pods
kubectl -n ops-agent delete pod -l app.kubernetes.io/name=ops-agent
kubectl -n ops-agent get pods -w
```

新 Pod 会在十几秒内起来。**这就是"期望状态 vs 实际状态"的调和循环**——
面试问"k8s 怎么保证高可用"，答这个，别答"因为有多副本"。

### 4.3 滚动更新

改个环境变量（比如把 `RECURSION_LIMIT` 从 16 改成 20），重新 apply：

```bash
kubectl -n ops-agent set env deploy/api RECURSION_LIMIT=20
kubectl -n ops-agent rollout status deploy/api
kubectl -n ops-agent rollout history deploy/api
```

**滚动更新不中断服务**，因为 `maxUnavailable: 0` + readinessProbe：新 Pod
就绪前旧 Pod 不会下线。回滚：

```bash
kubectl -n ops-agent rollout undo deploy/api
```

### 4.4 扩缩容

```bash
kubectl -n ops-agent scale deploy/api --replicas=3
kubectl -n ops-agent get pods -o wide
kubectl -n ops-agent scale deploy/api --replicas=1
```

---

## 5. 可选：Prometheus + Grafana

JD 里写了监控，但完整 kube-prometheus-stack 太重（十几个 Pod，WSL 容易 OOM）。
最小可用版是「Prometheus 抓 kubelet + Grafana 看图」，用 helm 一条命令：

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 只装 server + node-exporter + grafana，关掉告警和一堆 exporter
helm install monitor prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.retention=6h
```

装完（约 3–5 分钟）：

```bash
kubectl -n monitoring get pods
kubectl -n monitoring port-forward svc/monitor-grafana 3000:80
```

浏览器开 `http://localhost:3000`，账号 `admin` / 密码用下面这条取：

```bash
kubectl -n monitoring get secret monitor-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

进 Grafana → Dashboards → 搜 `Kubernetes / Compute Resources / Cluster`，
能看到集群 CPU / 内存 / Pod 数曲线。截图存下来，面试时能讲"我配过监控"。

> **内存不够就别装**。k3s 空跑约 500M，这套监控再加 1.5G 左右。
> 简历上没写 Prometheus，不装也不影响投递。

---

## 6. 常用命令速查

| 目的 | 命令 |
|---|---|
| 看 Pod | `kubectl -n ops-agent get pods -o wide` |
| 看日志 | `kubectl -n ops-agent logs -f deploy/api` |
| 进容器 | `kubectl -n ops-agent exec -it deploy/api -- sh` |
| 看事件（排障第一步） | `kubectl -n ops-agent get events --sort-by=.lastTimestamp` |
| 看配置 | `kubectl -n ops-agent get cm,secret` |
| 端口转发 | `kubectl -n ops-agent port-forward svc/api 8000:8000` |
| 删掉整套 | `kubectl delete -f k8s/` |

**排障顺序固定**：`get pods` 看状态 → 不是 Running 就 `describe pod` 看 Events
→ 起来了但 500 就 `logs`。三步覆盖 90% 的问题。

---

## 7. 常见报错

| 现象 | 原因 | 处理 |
|---|---|---|
| Pod `ImagePullBackOff` | 镜像是本地构建的，k3s 拉不到 | `sudo k3s ctr images import` 重新导入 |
| Pod `Pending` | PVC 没绑上 | `kubectl get pvc`，k3s 的 local-path 会自动绑，超过 1 分钟就看 `describe pvc` |
| `CreateContainerConfigError` | Secret 没建或键名不对 | `kubectl get secret ops-agent-secret -o yaml` 检查 |
| `CrashLoopBackOff` | Key 无效 / 缺依赖 | `kubectl logs deploy/api --previous` 看上一轮的报错 |
| `k3s: command not found` | 装完没重新登录 shell | `source ~/.bashrc` 或重开终端 |
| systemd 起不来 | `/etc/wsl.conf` 没生效 | Windows 里 `wsl --shutdown` 再进 |

---

## 8. 简历可以怎么写（跑通之后）

**只写你真正做过的**。跑通上面第 1–4 节，下面这句就是真话：

> **容器化与编排**：将服务容器化后用 k3s 单节点集群部署，编写 Deployment /
> Service / ConfigMap / PVC 清单，配置 readiness / liveness 探针与滚动更新
> （`maxUnavailable: 0`），并通过删除 Pod 验证自愈、`rollout undo` 验证回滚。

如果要更短，一行版简历里写成：

> Docker 与 k3s（Deployment / Service / ConfigMap / PVC、探针、滚动更新与回滚）

**不要写"精通 K8S"**。你现在是"部署过一个自己的服务、知道探针和滚动更新
怎么配"的水平，面试官问"StatefulSet 和 Deployment 什么区别""网络插件用的
哪个、为什么"你要能答（k3s 默认 flannel + traefik），再往上就说没在生产
大规模集群待过。
