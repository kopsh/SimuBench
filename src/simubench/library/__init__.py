"""Built-in component library and instrument definitions.

Importing the package registers the built-in component types (``components``) in the engine's
registry: the engine never imports the library (the layers point one way, D87).
"""

from . import components as components
