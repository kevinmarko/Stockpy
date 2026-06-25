import numpy as np
import pandas as pd
from pykalman import KalmanFilter

class KalmanHedgeRatio:
    def __init__(self, transition_covariance_multiplier: float = 1e-5, observation_covariance: float = 1e-3):
        self.trans_cov = transition_covariance_multiplier * np.eye(2)
        self.obs_cov = observation_covariance
        self.initial_state_mean = np.zeros(2)
        self.initial_state_covariance = np.ones((2, 2))
        
    def estimate_hedge_ratio(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """
        Estimate time-varying alpha and beta using pykalman.
        Returns a DataFrame with columns 'alpha' and 'beta' aligned to the inputs.
        This uses the forward Kalman Filter (kf.filter) which is lookahead-free.
        """
        common_idx = y.index.intersection(x.index)
        y_aligned = y.loc[common_idx].values
        x_aligned = x.loc[common_idx].values
        
        N = len(common_idx)
        if N == 0:
            return pd.DataFrame(columns=['alpha', 'beta'])
            
        # Construct time-varying observation matrix [1, x_t]
        obs_matrices = np.zeros((N, 1, 2))
        obs_matrices[:, 0, 0] = 1.0
        obs_matrices[:, 0, 1] = x_aligned
        
        kf = KalmanFilter(
            transition_matrices=np.eye(2),
            observation_matrices=obs_matrices,
            transition_covariance=self.trans_cov,
            observation_covariance=self.obs_cov,
            initial_state_mean=self.initial_state_mean,
            initial_state_covariance=self.initial_state_covariance
        )
        
        state_means, _ = kf.filter(y_aligned)
        
        result = pd.DataFrame(index=common_idx)
        result['alpha'] = state_means[:, 0]
        result['beta'] = state_means[:, 1]
        return result

class KalmanHedgeRatioTracker:
    def __init__(self, transition_covariance_multiplier: float = 1e-5, observation_covariance: float = 1e-3):
        self.trans_cov = transition_covariance_multiplier * np.eye(2)
        self.obs_cov = observation_covariance
        self.state_mean = np.zeros(2)
        self.state_covariance = np.ones((2, 2))
        
    def update(self, y_val: float, x_val: float) -> tuple[float, float]:
        """
        Perform a single-step online Kalman filter update.
        Returns the updated (alpha, beta).
        """
        # 1. Prediction step
        state_mean_prior = self.state_mean
        state_covariance_prior = self.state_covariance + self.trans_cov
        
        # 2. Measurement step
        H = np.array([[1.0, x_val]])
        y_pred = H @ state_mean_prior
        innovation = y_val - y_pred[0]
        
        # 3. Innovation covariance
        S = H @ state_covariance_prior @ H.T + self.obs_cov
        
        # 4. Kalman Gain
        K = state_covariance_prior @ H.T / S[0, 0]
        
        # 5. Update state
        self.state_mean = state_mean_prior + K.flatten() * innovation
        self.state_covariance = (np.eye(2) - K @ H) @ state_covariance_prior
        
        return self.state_mean[0], self.state_mean[1]
