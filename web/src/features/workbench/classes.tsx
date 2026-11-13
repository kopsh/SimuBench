import {
  Activity,
  AudioWaveform,
  BarChart3,
  BatteryCharging,
  Cpu,
  Crosshair,
  Gauge,
  Lightbulb,
  Radio,
  SlidersHorizontal,
  SquareFunction,
  Target,
  SunDim,
  Waves,
  Zap,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import type { InstrumentInfo, SessionInfo } from "@/api/client";
import { GeneratorPanel } from "./screens/GeneratorPanel";
import { LoadScreen } from "./screens/LoadScreen";
import { LockinScreen } from "./screens/LockinScreen";
import { RfGeneratorScreen } from "./screens/RfGeneratorScreen";
import { ScopeScreen } from "./screens/ScopeScreen";
import { SmuScreen } from "./screens/SmuScreen";
import { SpectrumScreen } from "./screens/SpectrumScreen";

export interface ScreenProps {
  session: SessionInfo;
  inst: InstrumentInfo;
  readonly: boolean;
}

interface ClassEntry {
  icon: typeof Activity;
  /** The class's own front-panel screen; a class without one uses its definition's panel or the generic layout. */
  screen?: ComponentType<ScreenProps>;
}

/**
 * The one list of instrument classes the Web knows (#688). The icon (canvas, tree, tabs, home) and
 * the screen (the Screen picker) come from here; the label is `acceptance.class.<id>` in zh/en,
 * checked against these ids by `classes.test.ts`. A new class is declared here and in the two
 * i18n files, nowhere else.
 */
export const INSTRUMENT_CLASSES = {
  oscilloscope: { icon: Activity, screen: ScopeScreen },
  function_generator: {
    icon: AudioWaveform,
    screen: ({ session, inst, readonly }) => <GeneratorPanel sid={session.id} inst={inst} readonly={readonly} />,
  },
  multimeter: { icon: Gauge },
  lockin: {
    icon: Crosshair,
    screen: ({ session, inst, readonly }) => <LockinScreen sid={session.id} inst={inst} readonly={readonly} />,
  },
  spectrum_analyzer: {
    icon: BarChart3,
    screen: ({ session, inst, readonly }) => <SpectrumScreen sid={session.id} inst={inst} readonly={readonly} />,
  },
  rf_generator: {
    icon: Radio,
    screen: ({ session, inst, readonly }) => <RfGeneratorScreen sid={session.id} inst={inst} readonly={readonly} />,
  },
  smu: {
    icon: SquareFunction,
    screen: ({ session, inst, readonly }) => <SmuScreen sid={session.id} inst={inst} readonly={readonly} />,
  },
  electronic_load: {
    icon: BatteryCharging,
    screen: ({ session, inst, readonly }) => <LoadScreen sid={session.id} inst={inst} readonly={readonly} />,
  },
  source: { icon: Waves },
  daq: { icon: Cpu },
  frequency_counter: { icon: Cpu },
  calibrator: { icon: Target },
  power_supply: { icon: Zap },
  tunable_laser: { icon: Lightbulb },
  optical_power_meter: { icon: SunDim },
  optical_attenuator: { icon: SlidersHorizontal },
} satisfies Record<string, ClassEntry>;

export type InstrumentClassId = keyof typeof INSTRUMENT_CLASSES;
export const CLASS_IDS = Object.keys(INSTRUMENT_CLASSES) as InstrumentClassId[];

function entry(cls: string | undefined): ClassEntry | undefined {
  return cls !== undefined && Object.hasOwn(INSTRUMENT_CLASSES, cls) ? INSTRUMENT_CLASSES[cls as InstrumentClassId] : undefined;
}

/** The class's own screen as an element, or null when it has none. */
export function classScreen(cls: string | undefined, props: ScreenProps): ReactNode {
  const Screen = entry(cls)?.screen;
  return Screen ? <Screen {...props} /> : null;
}

/** The class's icon as an element (a component picked at render would be rebuilt on every render). */
export function ClassIcon({ cls, className }: { cls: string | undefined; className?: string }) {
  const Icon = entry(cls)?.icon ?? Cpu;
  return <Icon className={className} />;
}
