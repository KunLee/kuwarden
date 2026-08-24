/**
 * Light or dark, chosen explicitly and remembered.
 *
 * The palette for both has been in `index.css` since the beginning; nothing in the application
 * ever set `data-theme`, so the dark half was unreachable. This is the switch, not a new theme.
 *
 * **The system preference is deliberately not consulted.** An operations console that changes
 * appearance because of an OS setting is a console two people describe differently while
 * looking at the same screen — and screenshots in an incident thread stop matching what the
 * next person opens. Light is the default; dark is opt-in and sticky.
 */

import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "kuwarden.theme";

/** Read the stored choice. Anything unrecognised is light, so a corrupt value cannot leave the
 *  console in a state the toggle disagrees with. */
function stored(): Theme {
  return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
}

/**
 * Apply a theme to the document root.
 *
 * Exported so `main.tsx` can call it before React mounts. Applying it only from an effect
 * paints the light palette for one frame first, which reads as a flash on every reload for
 * anyone using dark.
 */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function initTheme(): void {
  applyTheme(stored());
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(stored);

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);

  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}

export type Density = "comfortable" | "presentation";

const DENSITY_KEY = "kuwarden.density";

/**
 * How much room the console gives itself.
 *
 * The default is dense on purpose — an operator scanning fifty runs wants rows, not air.
 * `presentation` scales the root font size, and because every size in the type scale is
 * `rem`, that one declaration moves the whole interface together.
 *
 * It exists because the same screen is read at two very different sizes: at a desk, and in a
 * recording or on a projector where 11px state labels are unreadable. Two settings is an
 * honest answer to that; picking a middle size that serves neither is not.
 */
function storedDensity(): Density {
  return localStorage.getItem(DENSITY_KEY) === "presentation" ? "presentation" : "comfortable";
}

export function applyDensity(density: Density): void {
  document.documentElement.dataset.density = density;
}

export function initDensity(): void {
  applyDensity(storedDensity());
}

export function useDensity(): [Density, () => void] {
  const [density, setDensity] = useState<Density>(storedDensity);

  useEffect(() => {
    applyDensity(density);
    localStorage.setItem(DENSITY_KEY, density);
  }, [density]);

  return [
    density,
    () => setDensity(density === "presentation" ? "comfortable" : "presentation"),
  ];
}
