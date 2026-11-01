import { createBrowserRouter } from "react-router-dom";
import { HomePage } from "./features/home/HomePage";
import { InstrumentPage } from "./features/instrument/InstrumentPage";
import { RunPage } from "./features/runs/RunPage";
import { WorkbenchPage } from "./features/workbench/WorkbenchPage";

export const router = createBrowserRouter([
  { path: "/", element: <HomePage /> },
  { path: "/s/:sid", element: <WorkbenchPage /> },
  { path: "/i/:defId", element: <InstrumentPage /> },
  { path: "/runs/:id", element: <RunPage /> },
  { path: "*", element: <HomePage /> },
]);
