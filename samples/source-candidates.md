# Simulink LLM-Wiki 首批资料清单

## MathWorks 官方资料：只放本地知识库，不随开源仓库分发

1. **Simulink Getting Started Guide R2025b**
   - URL: https://www.mathworks.com/help/pdf_doc/simulink/simulink_gs.pdf
   - 建议文件名: `simulink_gs_R2025b.pdf`
   - 用途: 测试长PDF、目录、图片、跨章节检索。
   - 上传参数: `parse_mode=auto`, `release=R2025b`, `language=en`。

2. **Fixed Step Solvers in Simulink**
   - URL: https://www.mathworks.com/help/simulink/ug/fixed-step-solvers-in-simulink.html
   - 建议在浏览器打印为: `fixed_step_solvers.pdf`
   - 用途: 测试公式、表格、求解器术语和精确引用。
   - 上传参数: `parse_mode=auto`, `release=R2025b`, `language=en`。

3. **Compare Solvers**
   - URL: https://www.mathworks.com/help/simulink/ug/compare-solvers.html
   - 建议在浏览器打印为: `compare_solvers.pdf`
   - 用途: 与固定步长资料形成多文档、多跳和冲突消解测试。

## 可随开源Demo提供的材料：下载时同时保留许可证

4. **Simulink Agentic Toolkit**
   - URL: https://github.com/matlab/simulink-agentic-toolkit
   - 建议保存: `README.md`, `GETTING_STARTED.md`, `LICENSE.md`
   - 许可证: MathWorks BSD-3-Clause，以仓库内 `LICENSE.md` 为准。
   - 用途: 测试Markdown、代码、工具说明和Wiki链接生成。

5. **Build a Traffic Light with Arduino**
   - URL: https://github.com/mathworks/Build-a-Traffic-Light-with-Arduino
   - 建议保存: `README.md`, `License.txt`
   - 许可证: BSD-3-Clause，以仓库内许可证为准。
   - 用途: 测试Stateflow、Simulink、代码生成和硬件部署主题。

## 投放方式

先将文件放到 `knowledge/raw/_manual_inbox/`，再从 `http://localhost:13000` 的“导入”页上传。不要将MathWorks帮助PDF提交到公开Git仓库。
