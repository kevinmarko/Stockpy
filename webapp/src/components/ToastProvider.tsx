import React from 'react';
import { Toaster } from 'react-hot-toast';
import { theme } from '../theme';

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <>
      {children}
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: theme.surface2,
            color: theme.textPrimary,
            border: `1px solid ${theme.border}`,
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.5)',
            fontSize: 'var(--t-body)',
            padding: '12px 16px',
            maxWidth: '400px',
          },
          success: {
            iconTheme: {
              primary: theme.growth,
              secondary: theme.surface2,
            },
          },
          error: {
            iconTheme: {
              primary: theme.decline,
              secondary: theme.surface2,
            },
            duration: 6000,
          },
          duration: 4000,
        }}
      />
    </>
  );
};
