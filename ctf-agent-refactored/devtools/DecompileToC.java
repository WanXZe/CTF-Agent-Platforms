// Export all non-external functions to C pseudocode after headless analysis.
// @category CTF
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public class DecompileToC extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected output C path");
        DecompInterface decompiler = new DecompInterface();
        int exported = 0;
        try {
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Cannot open program");
            try (PrintWriter writer = new PrintWriter(Files.newBufferedWriter(Path.of(args[0]), StandardCharsets.UTF_8))) {
                FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
                while (functions.hasNext() && !monitor.isCancelled()) {
                    Function function = functions.next();
                    if (function.isExternal()) continue;
                    DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                    if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                        writer.println("/* " + function.getName() + " @ " + function.getEntryPoint() + " */");
                        writer.println(result.getDecompiledFunction().getC());
                        exported++;
                    }
                }
            }
        } finally {
            decompiler.dispose();
        }
        if (exported == 0) throw new IllegalStateException("No functions decompiled");
        println("Exported " + exported + " functions to " + args[0]);
    }
}
