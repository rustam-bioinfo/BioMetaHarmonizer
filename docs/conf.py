# Configuration file for BioMetaHarmonizer Sphinx documentation.

project = "BioMetaHarmonizer"
copyright = "2024, Rustam"
author = "Rustam"
release = "0.6.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

html_theme = "sphinx_rtd_theme"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
}

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

napoleon_google_docstring = False
napoleon_numpy_docstring = True
