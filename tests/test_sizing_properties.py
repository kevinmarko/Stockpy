import math
from typing import Dict, Optional
import pytest
from hypothesis import given, assume, strategies as st
import numpy as np

from sizing.kelly import fractional_kelly
from sizing.vol_target import volatility_target_weight
from sizing.position_sizer import size_position, apply_portfolio_gross_cap

@given(
    p=st.one_of(st.floats(min_value=0.0, max_value=1.0), st.just(float('nan'))),
    b=st.one_of(st.floats(min_value=-10.0, max_value=100.0), st.just(float('nan'))),
    fraction=st.floats(min_value=0.0, max_value=2.0),
    cap=st.floats(min_value=0.01, max_value=1.0)
)
def test_fractional_kelly_properties(p, b, fraction, cap):
    res = fractional_kelly(p, b, fraction=fraction, cap=cap)
    
    if p is None or b is None or math.isnan(p) or math.isnan(b):
        assert math.isnan(res)
    elif b <= 0:
        assert res == 0.0
    else:
        assert not math.isnan(res)
        assert 0.0 <= res <= cap
        if fraction == 0.0:
            assert res == 0.0

@given(
    realized_vol=st.one_of(st.floats(min_value=-1.0, max_value=10.0), st.just(float('nan'))),
    target_vol=st.floats(min_value=0.0, max_value=1.0),
    max_leverage=st.floats(min_value=0.1, max_value=5.0)
)
def test_volatility_target_weight_properties(realized_vol, target_vol, max_leverage):
    res = volatility_target_weight(realized_vol, target_vol=target_vol, max_leverage=max_leverage)
    
    if realized_vol is None or math.isnan(realized_vol):
        assert math.isnan(res)
    elif realized_vol <= 0:
        assert res == max_leverage
    else:
        assert not math.isnan(res)
        assert 0.0 <= res <= max_leverage

@given(
    pre_regime_weight=st.floats(min_value=0.0, max_value=1.0),
    regime_multiplier=st.floats(min_value=0.0, max_value=2.0),
    meta_label_composite=st.floats(min_value=0.0, max_value=2.0),
    etf_transmission_multiplier=st.floats(min_value=0.0, max_value=2.0) | st.none() | st.just(float('nan')),
    max_position_weight=st.floats(min_value=0.01, max_value=1.0)
)
def test_size_position_properties(
    pre_regime_weight, regime_multiplier, meta_label_composite, 
    etf_transmission_multiplier, max_position_weight
):
    pre_regime_weight = min(pre_regime_weight, max_position_weight)
    
    res = size_position(
        pre_regime_weight=pre_regime_weight,
        regime_multiplier=regime_multiplier,
        meta_label_composite=meta_label_composite,
        etf_transmission_multiplier=etf_transmission_multiplier,
        max_position_weight=max_position_weight,
        path_tag="test",
        raw_weight=pre_regime_weight
    )
    
    assert 0.0 <= res.final_weight <= max_position_weight
    
    if (regime_multiplier <= 1.0 and 
        meta_label_composite <= 1.0 and 
        (etf_transmission_multiplier is None or etf_transmission_multiplier <= 1.0 or (isinstance(etf_transmission_multiplier, float) and math.isnan(etf_transmission_multiplier)))):
        assert res.final_weight <= pre_regime_weight + 1e-7

@given(
    weights_dict=st.dictionaries(
        st.text(min_size=1, max_size=5), 
        st.one_of(st.floats(min_value=-2.0, max_value=2.0), st.just(float('nan')), st.just(float('inf')), st.just(-float('inf'))),
        max_size=10
    ),
    max_gross=st.floats(min_value=0.01, max_value=5.0)
)
def test_apply_portfolio_gross_cap_properties(weights_dict, max_gross):
    res = apply_portfolio_gross_cap(weights_dict, max_gross=max_gross)
    
    finite_weights = {k: v for k, v in weights_dict.items() if v is not None and math.isfinite(v)}
    
    if not finite_weights:
        assert res.scale_factor == 1.0
        return
        
    gross = sum(abs(v) for v in finite_weights.values())
    
    assert res.scale_factor <= 1.0 + 1e-9
    
    if gross <= max_gross:
        assert abs(res.scale_factor - 1.0) < 1e-9
        assert res.was_capped is False
    else:
        assert res.scale_factor < 1.0
        assert res.was_capped is True
        
    for k, v in res.scaled_weights.items():
        if v is not None and math.isfinite(v):
            assert abs(v) <= max_gross + 1e-9
