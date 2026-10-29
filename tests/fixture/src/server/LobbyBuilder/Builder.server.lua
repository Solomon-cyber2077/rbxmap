--!strict
-- Builds the lobby area: floor, walls, spawn pad and the lobby lighting mood.
-- Runs once on server start.

local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.Shared.Config)

local LOBBY_SIZE = Config.LOBBY_SIZE

local function makePart(name: string, size: Vector3, cf: CFrame): Part
	local p = Instance.new("Part")
	p.Name = name
	p.Anchored = true
	p.Size = size
	p.CFrame = cf
	p.Parent = workspace
	return p
end

local function buildFloor()
	local floor = makePart("LobbyFloor", Vector3.new(LOBBY_SIZE, 1, LOBBY_SIZE), CFrame.new(0, 0, 0))
	floor.Material = Enum.Material.SmoothPlastic
	local label = if LOBBY_SIZE > 100 then "big" else "small"
	floor:SetAttribute("Size", label)
end

local function buildWalls()
	for i = 1, 4 do
		local angle = math.rad(90 * i)
		local wall = makePart("Wall" .. i, Vector3.new(LOBBY_SIZE, 20, 1), CFrame.Angles(0, angle, 0))
		if i % 2 == 0 then
			wall.Transparency = 0.2
		end
	end
	-- note: the word end inside a comment must not break function ranges
	local s = "end end end"
end

local function setLighting()
	Lighting.ClockTime = 14
	Lighting.Brightness = 2
	Lighting.Ambient = Color3.fromRGB(120, 120, 140)
	Lighting.FogEnd = 500
	Lighting.FogColor = Color3.fromRGB(200, 210, 230)

	local atmosphere = Lighting:FindFirstChildOfClass("Atmosphere") or Instance.new("Atmosphere")
	atmosphere.Density = 0.3
	atmosphere.Haze = 1.5
	atmosphere.Parent = Lighting

	local cc = Instance.new("ColorCorrectionEffect")
	cc.Saturation = 0.1
	cc.Contrast = 0.05
	cc.Parent = Lighting
end

local function makeSpawn()
	local spawnPad = Instance.new("SpawnLocation")
	spawnPad.Anchored = true
	spawnPad.Size = Vector3.new(8, 1, 8)
	spawnPad.Parent = workspace
end

local function buildLobby()
	buildFloor()
	buildWalls()
	makeSpawn()
	setLighting()
end

buildLobby()
