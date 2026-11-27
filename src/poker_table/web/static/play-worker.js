// The demo's table, off the page's thread: Python (Pyodide) and poker_table run here, and the
// page talks to it with postMessage. A module worker, because Pyodide's loader is a module: in a
// classic worker it never finished loading.
//
// Messages in: {id, op: "load", pyodide, wheel, expect}, {id, op: "open", table}, or a request
// for the table ({id, op: "step" | "hello" | "about" | "get" | "post", ...}, see demo_play.py).
// Messages out: {id, result} or {id, error}, and {progress, stage} while loading.

let loading = null, demo = null, table = null;

// What has been downloaded, for the progress bar. Pyodide fetches its files and the wheel with
// fetch(), so counting what comes through it counts them (the bytes after decompression).
let received = 0, counting = false;
function countDownloads(expect) {
  if (counting) return;
  counting = true;
  const plain = self.fetch.bind(self);
  self.fetch = async (input, init) => {
    const response = await plain(input, init);
    if (!response.body) return response;
    const counted = response.body.pipeThrough(new TransformStream({
      transform(chunk, out) {
        received += chunk.byteLength;
        postMessage({ progress: Math.min(0.99, received / expect) });
        out.enqueue(chunk);
      },
    }));
    return new Response(counted, { status: response.status, statusText: response.statusText, headers: response.headers });
  };
}

async function load({ pyodide, wheel, expect }) {
  countDownloads(expect);
  postMessage({ stage: "Downloading Python" });
  const { loadPyodide } = await import(`${pyodide}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: pyodide });
  postMessage({ stage: "Installing the table" });
  await py.loadPackage("micropip", { messageCallback: () => {} });
  // Only the table itself: its dependencies are the web server and the model SDKs, which the
  // demo has no use for and the browser could not run.
  await py.pyimport("micropip").install.callKwargs(wheel, { deps: false });
  demo = py.pyimport("poker_table.web.demo_play");
  postMessage({ progress: 1, stage: "Ready" });
}

self.onmessage = async ({ data }) => {
  const { id, op } = data;
  try {
    let result;
    if (op === "load") {
      loading = loading || load(data);
      await loading;
    } else if (op === "open") {
      await loading;
      if (table) table.destroy();
      table = demo.open_table(JSON.stringify(data.table));
      result = JSON.parse(table.page('{"op": "about"}')).body;
    } else {
      result = JSON.parse(table.page(JSON.stringify(data)));
    }
    postMessage({ id, result });
  } catch (exc) {
    if (op === "load") loading = null;  // so that pressing again tries again
    postMessage({ id, error: String((exc && exc.message) || exc) });
  }
};
