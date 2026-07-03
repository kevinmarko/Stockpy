import sys
import types
from unittest.mock import MagicMock

# Create a mock module
mock_tf = types.ModuleType('tensorflow')
mock_keras = types.ModuleType('tensorflow.keras')
mock_models = types.ModuleType('tensorflow.keras.models')
mock_layers = types.ModuleType('tensorflow.keras.layers')
mock_callbacks = types.ModuleType('tensorflow.keras.callbacks')

mock_tf.keras = mock_keras
mock_keras.models = mock_models
mock_keras.layers = mock_layers
mock_keras.callbacks = mock_callbacks

# mock_sequential can be a MagicMock since it's a class/attribute, not a module!
mock_sequential = MagicMock()
mock_models.Sequential = mock_sequential

sys.modules['tensorflow'] = mock_tf
sys.modules['tensorflow.keras'] = mock_keras
sys.modules['tensorflow.keras.models'] = mock_models
sys.modules['tensorflow.keras.layers'] = mock_layers
sys.modules['tensorflow.keras.callbacks'] = mock_callbacks

import matplotlib.pyplot as plt
plt.subplots()
print("Success")
