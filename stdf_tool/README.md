# STDF Analyzer

ATE 测试 STDF 文件的快速分析工具 · Web App (Streamlit) 版

支持 Advantest V93000 / Teradyne / 等 ATE 设备生成的 STDF (Standard Test Data Format) 文件。

## 功能

- **拖拽上传** `.stdf` 或 `.stdf.gz`
- **整体 KPI**：Yield / Pass / Fail / Test Count
- **Yield by Site**：站间良率对比，自动找 site-specific 失效
- **HBin / SBin Pareto**：失效模式分布
- **Top Failing Tests**：按失效次数排序，含 Cp / Cpk
- **Site × Test Heatmap**：定位站间不一致的测试项
- **Test Drilldown**：单个测试的参数分布直方图 + Box plot
- **Failed Parts**：失效 die 清单 + 每颗失效测试明细
- **Excel 导出**：8 sheet 完整分析报告
- **CSV 导出**：失效 die 清单

## 安装

```bash
# Python 3.9+
pip install -r requirements.txt
```

## 启动

**方式 A：直接命令**

```bash
streamlit run app.py
```

**方式 B：脚本**

- Linux / Mac: `./run.sh`
- Windows: 双击 `run.bat`

浏览器会自动打开 http://localhost:8501

## 大文件支持

默认上传上限 500 MB（已在 `.streamlit/config.toml` 配置）。
更大文件请修改 `maxUploadSize`。

## 内网部署给团队用

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

然后局域网内访问 `http://<本机IP>:8501`

## Docker 部署（可选）

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0"]
```

```bash
docker build -t stdf-analyzer .
docker run -p 8501:8501 stdf-analyzer
```

## 项目结构

```
stdf_tool/
├── app.py              # Streamlit UI 主入口
├── stdf_analyzer.py    # STDF 解析与分析核心 (可复用 class)
├── requirements.txt    # Python 依赖
├── run.sh / run.bat    # 启动脚本
├── .streamlit/
│   └── config.toml     # 上传上限等配置
└── README.md
```

## 作为 Python 库使用

```python
from stdf_analyzer import STDFAnalyzer

a = STDFAnalyzer()
a.parse("your_file.stdf")

print(a.get_kpi())                  # 概览 dict
print(a.get_site_yield())            # site 良率 DataFrame
print(a.get_top_failing_tests(20))   # Top 失效测试
a.export_excel("report.xlsx")        # 导出 Excel
```

## License

MIT
