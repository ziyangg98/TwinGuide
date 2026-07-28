SPHINXBUILD ?= python3 -m sphinx
SOURCEDIR = .
BUILDDIR = _build

.PHONY: html clean

html: clean
	$(SPHINXBUILD) -E -W --keep-going -b html "$(SOURCEDIR)" "$(BUILDDIR)/html"

clean:
	rm -rf "$(BUILDDIR)"
