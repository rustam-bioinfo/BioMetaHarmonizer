from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

with open("requirements.txt", "r") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="biometaharmonizer",
    version="0.1.0",
    author="Rustam",
    description="Universal NCBI BioSample metadata harmonization tool for genomic epidemiology.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/rustam-bioinfo/BioMetaHarmonizer",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.9",
    install_requires=requirements,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
    ],
)
