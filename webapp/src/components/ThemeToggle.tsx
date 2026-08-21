import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme } from "../context/ThemeContext";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  const cycleTheme = () => {
    if (theme === "dark") setTheme("light");
    else if (theme === "light") setTheme("system");
    else setTheme("dark");
  };

  const getIcon = () => {
    if (theme === "light") return <Sun size={16} />;
    if (theme === "dark") return <Moon size={16} />;
    return <Monitor size={16} />;
  };

  const getLabel = () => {
    if (theme === "light") return "Light Theme";
    if (theme === "dark") return "Dark Theme";
    return "System Theme";
  };

  return (
    <button
      onClick={cycleTheme}
      title={getLabel()}
      aria-label={getLabel()}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 28,
        height: 28,
        borderRadius: "var(--r-sm)",
        background: "transparent",
        border: "1px solid transparent",
        color: "var(--text-secondary)",
        cursor: "pointer",
        transition: "all 0.2s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--surface-2)";
        e.currentTarget.style.color = "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = "var(--text-secondary)";
      }}
    >
      {getIcon()}
    </button>
  );
}
