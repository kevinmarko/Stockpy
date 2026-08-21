import React from 'react';
import { theme, alpha } from '../../theme';

export type PayoffShape = 'call-spread' | 'put-spread' | 'straddle' | 'strangle' | 'calendar';

interface Props {
  type: PayoffShape;
}

export const OptionsPayoffChart: React.FC<Props> = ({ type }) => {
  // We'll draw simple SVG lines for the payoff shapes
  const w = 100;
  const h = 40;
  const mid = h / 2;

  let path = '';
  let fillPath = '';

  switch (type) {
    case 'call-spread':
      // Flat below, rises, flat above
      path = `M 0 ${mid + 10} L 40 ${mid + 10} L 60 ${mid - 15} L 100 ${mid - 15}`;
      fillPath = `${path} L 100 ${h} L 0 ${h} Z`;
      break;
    case 'put-spread':
      // Flat above, falls, flat below
      path = `M 0 ${mid - 15} L 40 ${mid - 15} L 60 ${mid + 10} L 100 ${mid + 10}`;
      fillPath = `${path} L 100 ${h} L 0 ${h} Z`;
      break;
    case 'straddle':
      // V-shape
      path = `M 10 ${mid + 15} L 50 ${mid - 15} L 90 ${mid + 15}`;
      fillPath = `${path} L 90 ${h} L 10 ${h} Z`;
      break;
    case 'strangle':
      // U-shape / flat bottom
      path = `M 10 ${mid + 15} L 40 ${mid - 5} L 60 ${mid - 5} L 90 ${mid + 15}`;
      fillPath = `${path} L 90 ${h} L 10 ${h} Z`;
      break;
    case 'calendar':
      // Bell shape
      path = `M 10 ${mid + 15} Q 50 ${mid - 25} 90 ${mid + 15}`;
      fillPath = `${path} L 90 ${h} L 10 ${h} Z`;
      break;
  }

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {/* Zero line (breakeven) */}
      <line x1="0" y1={mid} x2={w} y2={mid} stroke={theme.borderStrong} strokeWidth="1" strokeDasharray="2 2" />
      
      {/* Fill Area */}
      <path d={fillPath} fill={alpha(theme.accent, "20")} />
      
      {/* Payoff line */}
      <path d={path} fill="none" stroke={theme.accent} strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
};
