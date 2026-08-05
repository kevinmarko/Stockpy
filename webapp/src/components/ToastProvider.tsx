import React from 'react';
import { Toaster } from 'sonner';
import { theme } from '../theme';

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <>
      {children}
      <Toaster
        position="bottom-right"
        theme="dark"
        toastOptions={{
          style: {
            background: theme.surface2,
            color: theme.textPrimary,
            border: `1px solid ${theme.border}`,
            fontSize: 'var(--t-body)',
          },
        }}
      />
    </>
  );
};
