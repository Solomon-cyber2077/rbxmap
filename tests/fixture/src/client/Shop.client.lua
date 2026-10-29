-- Client shop UI: opens the shop frame and asks the server to buy items.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local Remotes = ReplicatedStorage:WaitForChild("Remotes")

local gui = Instance.new("ScreenGui")
gui.Name = "ShopGui"
local frame = Instance.new("Frame")
frame.Parent = gui
gui.Parent = player:WaitForChild("PlayerGui")

local function buy(itemId: string)
	Remotes.BuyItem:FireServer(itemId)
end

local button = Instance.new("TextButton")
button.Text = "Buy Sword"
button.Parent = frame
button.Activated:Connect(function()
	buy("Sword")
end)
