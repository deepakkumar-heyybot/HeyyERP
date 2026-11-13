import { createContext, useContext } from "react";
import { Unit } from "./api-client";

export type ActivePlantContextValue = {
  activePlantId: string;
  activeUnit: Unit | null;
  allPlants: boolean;
  selectActivePlant: (plantId: string) => void;
};

export const ActivePlantContext = createContext<ActivePlantContextValue | null>(null);

export function useActivePlant() {
  const context = useContext(ActivePlantContext);
  if (!context) throw new Error("useActivePlant must be used within the private application shell.");
  return context;
}
