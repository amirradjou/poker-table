"""The solvers behind the research seats: CFR+, best responses and a fast evaluator.

These modules need numpy, which is in the dev dependency group and not in the package's
dependencies. They are run by the scripts that build the seats' data files and by the tests.
The seats themselves (``agents/pushfold.py``, ``agents/blueprint.py``) only read those files and
never import anything from here, so an install without numpy plays them fine.
"""
