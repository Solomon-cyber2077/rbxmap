-- Handles shop purchases: developer products and the BuyItem remote.
local MarketplaceService = game:GetService("MarketplaceService")
local DataStoreService = game:GetService("DataStoreService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage.Shared.Config)
local Remotes = ReplicatedStorage:WaitForChild("Remotes")
local buyItem = Remotes:WaitForChild("BuyItem")
local store = DataStoreService:GetDataStore("Purchases")

local function grant(player: Player, itemId: string): boolean
	local price = Config.PRICES[itemId]
	if not price then
		return false
	end
	store:UpdateAsync(tostring(player.UserId), function(old)
		old = old or {}
		table.insert(old, itemId)
		return old
	end)
	return true
end

buyItem.OnServerEvent:Connect(function(player, itemId)
	grant(player, itemId)
end)

MarketplaceService.ProcessReceipt = function(receipt)
	return Enum.ProductPurchaseDecision.PurchaseGranted
end
