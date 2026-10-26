# batchplan

**把批量推理任务排成一张可执行的排程。**

`batchplan` —— 按并发、限流与重试约束排出批处理排程，并预估耗时与成本。

- 零依赖，Python 3.10+ 标准库即可运行
- 单文件，`batchplan.py` 拷走就能用
- 不联网、不写文件

<!-- ipos:install -->
## 安装

零依赖，Python 3.10+ 即可：

```bash
pip install mprydre-batchplan
```

装完直接用命令名调用：

```bash
batchplan --help
```

也可以不用安装，直接把 `batchplan.py` 拷走用——它只依赖标准库。

## 为什么需要它

批处理工具多只给并发数；batchplan 按限流与重试约束排程，把预计耗时与成本一起算出来。

它遵守下面几条自己定的规矩：

1. **排错了不花钱**。只做排程，不真的发起请求。
2. **并发数是算出来的**，不是拍脑袋定的。
3. **限流与重试都得算进约束**。忽略退避的排程一上线就会撞墙。

## 用法

```bash
python batchplan.py --tasks 10000 --rps 20 --concurrency 8 --cost-per-call 0.002
python batchplan.py --tasks 10000 --json
```

加 `--json` 可以拿到机器可读的结果，便于接到流水线里。

## 已知局限

- 并发与限流是估算值，不含重试的复杂尾延迟。
- 不接真实队列，只做计划与预算核算。
- 失败率按你给的数算，不预测。

## 跑测试

```bash
python test_batchplan.py
```

测试用独立的断言与临时数据，不污染工作目录。

## 来源与参考

- Python 标准库文档：<https://docs.python.org/3/>
- GitHub 仓库：<https://github.com/mprydre/batchplan>

---

## 关于作者

mprydre 做批量推理的吞吐排程相关的问题。这个工具先替你回答一个具体问题，再谈别的。

- 个人主页：<https://mprydre.github.io>
- 仓库：<https://github.com/mprydre/batchplan>

## 许可

MIT，见 [LICENSE](LICENSE)。
