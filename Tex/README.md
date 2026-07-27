# TwinGuide 中文技术报告

本目录是独立的 LaTeX 报告工程。

## 目录

- `main.tex`：报告入口。
- `chapters/`：分章节正文。
- `figures/`：报告实际引用的 tooth-47 中间结果与最终结果图。
- `build/`：XeLaTeX 编译产物。
- `tmp/pdfs/`：PDF 逐页渲染检查的临时图片。

## 编译

```bash
latexmk -xelatex -interaction=nonstopmode -halt-on-error -outdir=build main.tex
```

编译产物为 `build/main.pdf`；本次交付同时在工程根目录保留固定名称的
`TwinGuide技术报告.pdf`。报告使用 `ctexrep` 和 macOS 中文字体配置。

## 资料边界

报告依据 2026-07-27 工作区内的 TwinGuide 源码、配置、文档、测试，以及
`output/tooth_47` 中已有的过程产物撰写。现有 tooth-47 图片和 STL 的生成时间
早于部分源码和 YAML 的最后修改时间，因此它们用于解释流程和几何形态，不能替代
以当前版本重新执行 `generate --validate` 的正式回归验收。
