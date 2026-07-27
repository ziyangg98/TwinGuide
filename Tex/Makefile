LATEXMK = latexmk
MAIN = main.tex
OUT = build

.PHONY: all clean

all:
	$(LATEXMK) -xelatex -interaction=nonstopmode -halt-on-error -outdir=$(OUT) $(MAIN)

clean:
	$(LATEXMK) -C -outdir=$(OUT) $(MAIN)
