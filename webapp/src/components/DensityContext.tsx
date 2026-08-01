import React, { createContext, useContext, useState, useEffect } from "react";

export type LayoutDensity = "compact" | "spacious";

interface DensityContextValue {
  density: LayoutDensity;
  setDensity: (density: LayoutDensity) => void;
  toggleDensity: () => void;
}

const DensityContext = createContext<DensityContextValue | undefined>(undefined);

export const DensityProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [density, setDensityState] = useState<LayoutDensity>(() => {
    return (localStorage.getItem("layout_density") as LayoutDensity) || "spacious";
  });

  const setDensity = (d: LayoutDensity) => {
    setDensityState(d);
    localStorage.setItem("layout_density", d);
  };

  const toggleDensity = () => {
    setDensity(density === "spacious" ? "compact" : "spacious");
  };

  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
  }, [density]);

  return (
    <DensityContext.Provider value={{ density, setDensity, toggleDensity }}>
      {children}
    </DensityContext.Provider>
  );
};

export const useDensity = () => {
  const ctx = useContext(DensityContext);
  if (!ctx) {
    throw new Error("useDensity must be used within a DensityProvider");
  }
  return ctx;
};
