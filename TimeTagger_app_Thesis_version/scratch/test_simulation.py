import asyncio
import json
import websockets
import numpy as np

async def test_simulation():
    uri = "ws://localhost:8000/ws"
    print(f"Connecting to {uri}...")
    
    async with websockets.connect(uri) as websocket:
        # 1. Wait for initial config
        response = await websocket.recv()
        msg = json.loads(response)
        config = msg.get("config")
        print(f"Initial config loaded. Simulation mode: {config.get('simulation_mode')}")
        
        # Enable simulation mode if it is not already
        if not config.get("simulation_mode"):
            print("Enabling simulation mode...")
            new_cfg = dict(config)
            new_cfg["simulation_mode"] = True
            await websocket.send(json.dumps({
                "type": "save_config",
                "config": new_cfg
            }))
            # Wait for config response
            response = await websocket.recv()
            msg = json.loads(response)
            if msg.get("type") == "data":
                response = await websocket.recv()
                msg = json.loads(response)
            config = msg.get("config")
            print(f"Simulation mode enabled: {config.get('simulation_mode')}")

        # Sleep for a bit to accumulate counts
        print("Accumulating counts with simulation mode ON for 4 seconds...")
        histogram_vals_sim_on = []
        for _ in range(10):
            response = await websocket.recv()
            msg = json.loads(response)
            if msg.get("type") == "data":
                histogram_vals_sim_on = msg.get("histogram", {}).get("values", [])
            await asyncio.sleep(0.4)

        peak_on = max(histogram_vals_sim_on) if histogram_vals_sim_on else 0
        sum_on = sum(histogram_vals_sim_on) if histogram_vals_sim_on else 0
        print(f"Simulation ON: Peak counts = {peak_on}, Sum counts = {sum_on}")
        assert sum_on > 0, "Expected non-zero histogram counts when simulation mode is active"

        # Now toggle simulation mode OFF
        print("Disabling simulation mode...")
        new_cfg = dict(config)
        new_cfg["simulation_mode"] = False
        await websocket.send(json.dumps({
            "type": "save_config",
            "config": new_cfg
        }))
        
        # Wait for config response
        response = await websocket.recv()
        msg = json.loads(response)
        while msg.get("type") != "config":
            response = await websocket.recv()
            msg = json.loads(response)
        config = msg.get("config")
        print(f"Simulation mode is now: {config.get('simulation_mode')}")

        # Accumulating counts with simulation mode OFF
        print("Accumulating counts with simulation mode OFF for 4 seconds...")
        histogram_vals_sim_off = []
        for _ in range(10):
            response = await websocket.recv()
            msg = json.loads(response)
            if msg.get("type") == "data":
                histogram_vals_sim_off = msg.get("histogram", {}).get("values", [])
            await asyncio.sleep(0.4)

        peak_off = max(histogram_vals_sim_off) if histogram_vals_sim_off else 0
        sum_off = sum(histogram_vals_sim_off) if histogram_vals_sim_off else 0
        print(f"Simulation OFF: Peak counts = {peak_off}, Sum counts = {sum_off}")
        
        # Re-enabling simulation mode to restore initial settings
        print("Restoring simulation mode to ON...")
        new_cfg = dict(config)
        new_cfg["simulation_mode"] = True
        await websocket.send(json.dumps({
            "type": "save_config",
            "config": new_cfg
        }))
        print("Done!")

if __name__ == "__main__":
    asyncio.run(test_simulation())
