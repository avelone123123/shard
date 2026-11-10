import sys
from setuptools import setup, find_packages
setup(
    name="shard-engine",
    version="1.4.31",
    description="Tri-language local neural engine (Python + C++20 + MASM AVX2 Assembly)",
    author="avelone123123",
    author_email="ermekeraly16@gmail.com",
    url="https://github.com/avelone123123/shard",
    py_modules=["cli", "main", "server", "engine", "hardware", "hf_hub", "shard_bridge"],
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "fastapi>=0.100.0",
        "uvicorn>=0.22.0",
        "requests>=2.28.0",
        "psutil>=5.9.0",
        "pydantic>=2.0.0"
    ],
    entry_points={
        "console_scripts": [
            "shard=cli:main"
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: C++",
        "Operating System :: Microsoft :: Windows"
    ],
    python_requires=">=3.8"
)
