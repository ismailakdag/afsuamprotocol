"""
Setup script for AFSUAM Measurement System.
"""

from setuptools import setup, find_packages

setup(
    name="afsuam-measurement",
    version="2.0.0",
    description="AFSUAM Phased Array RFID Measurement System",
    author="AFSUAM Team",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "numpy>=1.20.0",
        "pandas>=1.3.0",
        "scipy>=1.7.0",
        "pyserial>=3.5",
        "sllurp>=0.5.0",
        "twisted>=21.0.0",
    ],
    extras_require={
        "plotting": ["matplotlib>=3.4.0"],
        "ml": ["scikit-learn>=0.24.0", "joblib>=1.0.0"],
    },
    entry_points={
        "console_scripts": [
            "afsuam-gui=gui.app:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
)
