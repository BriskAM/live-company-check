"use client";

import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

function current(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The active theme, following the OS until the reviewer picks one. */
export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    const update = () => setThemeState(current());
    update();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", update);
      observer.disconnect();
    };
  }, []);

  function setTheme(t: Theme) {
    document.documentElement.dataset.theme = t;
    try {
      localStorage.setItem("theme", t);
    } catch {
      // private window or blocked storage: the choice lasts for this page only
    }
  }

  return [theme, setTheme];
}

export type ChartColors = { series: string; grid: string; axis: string; surface: string; ink: string; muted: string };

/** Chart colors from the theme tokens. Recharts takes colors as SVG attributes, where CSS
 * variables don't apply, so they are read from the computed style and refreshed on theme change. */
export function useChartColors(): ChartColors {
  const [theme] = useTheme();
  const [colors, setColors] = useState<ChartColors>({
    series: "#8250df",
    grid: "#e6ebf1",
    axis: "#6e7781",
    surface: "#ffffff",
    ink: "#1f2328",
    muted: "#59636e",
  });
  useEffect(() => {
    const s = getComputedStyle(document.documentElement);
    const v = (name: string) => s.getPropertyValue(name).trim();
    setColors({
      series: v("--series"),
      grid: v("--grid"),
      axis: v("--axis"),
      surface: v("--card"),
      ink: v("--ink"),
      muted: v("--muted"),
    });
  }, [theme]);
  return colors;
}
