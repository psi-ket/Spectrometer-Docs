import asyncio
import json
import websockets
import os

async def test_server():
    uri = "ws://localhost:8000/ws"
    print(f"Connecting to {uri}...")
    
    async with websockets.connect(uri) as websocket:
        # 1. Wait for the initial config payload
        print("\nWaiting for initial config...")
        response = await websocket.recv()
        msg = json.loads(response)
        print("Received message type:", msg.get("type"))
        assert msg.get("type") == "config", f"Expected type 'config', got {msg.get('type')}"
        config = msg.get("config")
        print(f"Initial config loaded. Active channels: {config.get('active_channels')}")
        
        # 2. Wait for a data payload
        print("\nWaiting for first data broadcast...")
        response = await websocket.recv()
        msg = json.loads(response)
        print("Received message type:", msg.get("type"))
        assert msg.get("type") == "data", f"Expected type 'data', got {msg.get('type')}"
        cps_data = msg.get("cps")
        print(f"Active Channels: {cps_data.get('channels')}")
        print(f"Latest values: {cps_data.get('latest')}")
        assert len(cps_data.get("latest")) == len(config.get("active_channels"))
        
        # 3. Test changing configuration (Save Config)
        print("\nSending config update: change active channels to [1, 3, 5]")
        new_cfg = dict(config)
        new_cfg["active_channels"] = [1, 3, 5]
        new_cfg["channel_aliases"]["3"] = "Custom Ch 3"
        new_cfg["channel_aliases"]["5"] = "Custom Ch 5"
        new_cfg["integration_time_sec"] = 0.2
        
        await websocket.send(json.dumps({
            "type": "save_config",
            "config": new_cfg
        }))
        
        # Expect config updated broadcast
        print("Waiting for updated config broadcast...")
        response = await websocket.recv()
        msg = json.loads(response)
        print("Received type:", msg.get("type"))
        if msg.get("type") == "data":
            # Might get an interleaved data broadcast, wait for config
            response = await websocket.recv()
            msg = json.loads(response)
            print("Received type (after retry):", msg.get("type"))
            
        assert msg.get("type") == "config", f"Expected 'config', got {msg.get('type')}"
        updated_config = msg.get("config")
        print("Updated channels on server:", updated_config.get("active_channels"))
        assert updated_config.get("active_channels") == [1, 3, 5]
        assert updated_config.get("integration_time_sec") == 0.2
        
        # 4. Test starting recording
        print("\nSending start_recording...")
        await websocket.send(json.dumps({"type": "start_recording"}))
        
        response = await websocket.recv()
        msg = json.loads(response)
        print("Received type:", msg.get("type"))
        if msg.get("type") == "config" or msg.get("type") == "data":
            response = await websocket.recv()
            msg = json.loads(response)
            print("Received type (after retry):", msg.get("type"))
        
        assert msg.get("type") == "recording_started", f"Expected 'recording_started', got {msg.get('type')}"
        filename = msg.get("filename")
        print(f"Recording started on server. File: {filename}")
        
        # Wait for a couple of seconds to accumulate data
        print("Sleeping 2 seconds...")
        await asyncio.sleep(2)
        
        # Stop recording
        print("\nSending stop_recording...")
        await websocket.send(json.dumps({"type": "stop_recording"}))
        
        response = await websocket.recv()
        msg = json.loads(response)
        print("Received type:", msg.get("type"))
        while msg.get("type") not in ["recording_stopped"]:
            response = await websocket.recv()
            msg = json.loads(response)
            print("Received type (looping):", msg.get("type"))
            
        assert msg.get("type") == "recording_stopped", f"Expected 'recording_stopped', got {msg.get('type')}"
        stopped_filename = msg.get("filename")
        print(f"Recording stopped. File saved: {stopped_filename}")
        
        # Let's restore the original config to be clean
        print("\nRestoring original configuration...")
        await websocket.send(json.dumps({
            "type": "save_config",
            "config": config
        }))
        print("Restore sent.")

if __name__ == "__main__":
    asyncio.run(test_server())
