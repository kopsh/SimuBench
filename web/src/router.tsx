import { createBrowserRouter } from "react-router-dom";
import { HomePage } from "./features/home/HomePage";
import { WorkbenchPage } from "./features/workbench/WorkbenchPage";

export const router = createBrowserRouter([
  { path: "/", element: <HomePage /> },
  { path: "/s/:sid", element: <WorkbenchPage /> },
  { path: "*", element: <HomePage /> },
]);
